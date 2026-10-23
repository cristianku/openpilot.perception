package ch.bodhi.perception

import com.hierynomus.sshj.key.KeyAlgorithms
import com.hierynomus.sshj.transport.cipher.BlockCiphers
import com.hierynomus.sshj.transport.cipher.ChachaPolyCiphers
import com.hierynomus.sshj.transport.cipher.GcmCiphers
import com.hierynomus.sshj.transport.mac.Macs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import net.schmizz.sshj.DefaultConfig
import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.common.Buffer
import net.schmizz.sshj.common.Ed25519KeyFactory
import net.schmizz.sshj.common.Factory
import net.schmizz.sshj.common.KeyType
import net.schmizz.sshj.common.SecurityUtils
import net.schmizz.sshj.connection.channel.direct.Session
import net.schmizz.sshj.transport.TransportException
import net.schmizz.sshj.transport.cipher.Cipher
import net.schmizz.sshj.transport.kex.Curve25519SHA256
import net.schmizz.sshj.transport.kex.KeyExchange
import net.schmizz.sshj.transport.mac.MAC
import net.schmizz.sshj.transport.verification.HostKeyVerifier
import net.schmizz.sshj.userauth.UserAuthException
import net.schmizz.sshj.userauth.keyprovider.KeyProvider
import org.bouncycastle.jce.provider.BouncyCastleProvider
import java.io.ByteArrayOutputStream
import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.net.InetSocketAddress
import java.security.PrivateKey
import java.security.PublicKey
import java.security.Security
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.SocketFactory

/** The SSH side of the app: the comma's sshd, user comma, port 22. sshj (Apache-2.0) with Bouncy Castle. */
object CommaSsh {
    const val USER = "comma"
    const val PORT = 22

    @Volatile
    private var installed = false

    /** The full Bouncy Castle provider in place of Android's trimmed one, at the end of the list (the platform
     * keeps its own providers first); sshj asks it by name. */
    fun installCrypto() {
        if (installed) return
        synchronized(this) {
            if (installed) return
            Security.removeProvider(BouncyCastleProvider.PROVIDER_NAME)
            Security.addProvider(BouncyCastleProvider())
            SecurityUtils.setSecurityProvider(BouncyCastleProvider.PROVIDER_NAME)
            installed = true
        }
    }

    /** Modern algorithms only, and the host key as ssh-ed25519, the one whose fingerprint the app pins. */
    fun config(): DefaultConfig {
        installCrypto()
        return DefaultConfig().apply {
            setKeyExchangeFactories(listOf<Factory.Named<KeyExchange>>(Curve25519SHA256.Factory(), Curve25519SHA256.FactoryLibSsh()))
            keyAlgorithms = listOf(KeyAlgorithms.EdDSA25519())
            setCipherFactories(listOf<Factory.Named<Cipher>>(ChachaPolyCiphers.CHACHA_POLY_OPENSSH(), GcmCiphers.AES256GCM(),
                GcmCiphers.AES128GCM(), BlockCiphers.AES256CTR(), BlockCiphers.AES128CTR()))
            setMACFactories(listOf<Factory.Named<MAC>>(Macs.HMACSHA2256Etm(), Macs.HMACSHA2512Etm(), Macs.HMACSHA2256(), Macs.HMACSHA2512()))
            timeoutMs = 10_000
        }
    }

    /** SHA256:… of an SSH public key, as `ssh-keygen -l` prints it. */
    fun fingerprint(key: PublicKey): String = SshKeys.fingerprint(Buffer.PlainBuffer().putPublicKey(key).compactData)
}

/** Accepts the host key only when its fingerprint is trusted; remembers the key it was shown either way. */
class PinnedHostKey(private val trusted: Set<String>) : HostKeyVerifier {
    @Volatile
    var presented: String? = null
        private set

    override fun verify(hostname: String?, port: Int, key: PublicKey): Boolean {
        val fingerprint = CommaSsh.fingerprint(key)
        presented = fingerprint
        return fingerprint in trusted
    }

    override fun findExistingAlgorithms(hostname: String?, port: Int): List<String> = emptyList()
}

/** The phone key for one login, from its 32-byte seed. */
class SeedKeyProvider(seed: ByteArray) : KeyProvider {
    private val privateKey: PrivateKey
    private val publicKey: PublicKey

    init {
        CommaSsh.installCrypto()
        privateKey = Ed25519KeyFactory.getPrivateKey(seed)
        publicKey = Ed25519KeyFactory.getPublicKey(SshKeys.publicOf(seed))
    }

    override fun getPrivate(): PrivateKey = privateKey
    override fun getPublic(): PublicKey = publicKey
    override fun getType(): KeyType = KeyType.ED25519
}

/** Why an SSH connection to the comma did not work. */
sealed class SshProblem(message: String) : IOException(message) {
    class Unreachable(host: String) : SshProblem("Nothing answers at $host on port 22.")
    /** The host showed another key than the trusted one: never accepted. */
    class HostKeyRefused(val host: String, val presented: String?) :
        SshProblem("The SSH key of $host is not the comma's (${presented ?: "none shown"}).")
    class LoginRefused : SshProblem("The comma does not accept this phone's key yet.")
    class Failed(message: String) : SshProblem(message)
}

/** One SSH connection to the comma, authenticated with the phone key and the host key pinned. */
class CommaShell private constructor(private val client: SSHClient) : Closeable {
    private val closed = AtomicBoolean(false)

    companion object {
        /** Blocking: off the main thread. [seed] is wiped by the caller. */
        fun open(host: String, subnet: LocalSubnet?, trusted: Set<String>, seed: ByteArray, readTimeoutMs: Int = 15_000): CommaShell {
            val verifier = PinnedHostKey(trusted)
            val client = SSHClient(CommaSsh.config())
            client.connectTimeout = 5_000
            // No byte at all for this long (the listener sends one line a second at least): the link is dead.
            client.timeout = readTimeoutMs
            subnet?.socketFactory?.let { client.socketFactory = it }
            client.addHostKeyVerifier(verifier)
            try {
                client.connect(host, CommaSsh.PORT)
            } catch (error: TransportException) {
                client.closeQuietly()
                if (verifier.presented != null && verifier.presented !in trusted) throw SshProblem.HostKeyRefused(host, verifier.presented)
                throw SshProblem.Failed("The SSH connection to $host failed: ${error.message ?: "unknown"}.")
            } catch (_: IOException) {
                client.closeQuietly()
                throw SshProblem.Unreachable(host)
            }
            try {
                client.authPublickey(CommaSsh.USER, SeedKeyProvider(seed))
            } catch (_: UserAuthException) {
                client.closeQuietly()
                throw SshProblem.LoginRefused()
            } catch (error: Exception) {
                client.closeQuietly()
                throw SshProblem.Failed("The login on the comma failed: ${error.message ?: "unknown"}.")
            }
            return CommaShell(client)
        }

        private fun SSHClient.closeQuietly() {
            try {
                close()
            } catch (_: Exception) {
            }
        }
    }

    /** A short read-only command; its standard output (at most 4 KB). */
    fun run(command: String): String {
        client.startSession().use { session ->
            val cmd = session.exec(command)
            cmd.outputStream.close()
            val text = readBounded(cmd.inputStream, 4096)
            cmd.join(10, java.util.concurrent.TimeUnit.SECONDS)
            return text
        }
    }

    /** Starts the listener: the command, then the script on its standard input and the end of input (Python
     * starts the script then). */
    fun startListener(command: String, script: ByteArray): ListenerChannel {
        val session = client.startSession()
        try {
            val cmd = session.exec(command)
            cmd.outputStream.write(script)
            cmd.outputStream.flush()
            cmd.outputStream.close()
            return ListenerChannel(session, cmd)
        } catch (error: IOException) {
            session.close()
            throw error
        }
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        try {
            client.disconnect()
        } catch (_: Exception) {
        }
        try {
            client.close()
        } catch (_: Exception) {
        }
    }

    private fun readBounded(input: InputStream, limit: Int): String {
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            if (out.size() < limit) out.write(buffer, 0, minOf(count, limit - out.size()))
        }
        return out.toString(Charsets.UTF_8.name())
    }
}

/** The running listener: its lines, then close to stop it (its next write fails and it exits). */
class ListenerChannel(private val session: Session, private val command: Session.Command) : Closeable {
    private val closed = AtomicBoolean(false)
    private val errors = StringBuilder()

    init {
        // The standard error is read so it never stalls the channel; its end says why a start failed.
        Thread({
            try {
                val buffer = ByteArray(512)
                val input = command.errorStream
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    synchronized(errors) {
                        errors.append(String(buffer, 0, count, Charsets.UTF_8))
                        if (errors.length > 2000) errors.delete(0, errors.length - 2000)
                    }
                }
            } catch (_: Exception) {
            }
        }, "listener-stderr").apply { isDaemon = true }.start()
    }

    /** The end of the standard error, for a start that ended without a line. */
    fun errorText(): String = synchronized(errors) { errors.toString().trim().takeLast(300) }

    /** Blocking: every line until the listener ends or [close]; lines longer than 64 KB are dropped. */
    fun readLines(onLine: (String) -> Unit) {
        val input = command.inputStream
        val line = ByteArrayOutputStream()
        var tooLong = false
        val buffer = ByteArray(4096)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) return
            for (index in 0 until count) {
                val byte = buffer[index]
                if (byte == '\n'.code.toByte()) {
                    if (!tooLong) onLine(line.toString(Charsets.UTF_8.name()))
                    line.reset()
                    tooLong = false
                } else if (!tooLong) {
                    line.write(byte.toInt())
                    if (line.size() > 65_536) {
                        tooLong = true
                        line.reset()
                    }
                }
            }
        }
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        try {
            command.close()
        } catch (_: Exception) {
        }
        try {
            session.close()
        } catch (_: Exception) {
        }
    }
}

/** [CommaConnector] over the real network: a TCP connection for the port, the SSH key exchange (no login) for
 * the host key. */
class SshCommaConnector : CommaConnector {
    override suspend fun portOpen(host: String, port: Int, timeoutMs: Int, subnet: LocalSubnet?): Boolean =
        withContext(Dispatchers.IO) {
            try {
                (subnet?.socketFactory ?: SocketFactory.getDefault()).createSocket().use { socket ->
                    socket.connect(InetSocketAddress(host, port), timeoutMs)
                    true
                }
            } catch (_: IOException) {
                false
            } catch (_: SecurityException) {
                false
            }
        }

    override suspend fun hostKey(host: String, port: Int, subnet: LocalSubnet?): String? = withContext(Dispatchers.IO) {
        val verifier = PinnedHostKey(emptySet())
        val client = SSHClient(CommaSsh.config())
        client.connectTimeout = 2_000
        client.timeout = 4_000
        subnet?.socketFactory?.let { client.socketFactory = it }
        client.addHostKeyVerifier(verifier)
        try {
            client.connect(host, port)
        } catch (_: Exception) {
            // Refused by the verifier on purpose: only the key was wanted.
        } finally {
            try {
                client.close()
            } catch (_: Exception) {
            }
        }
        verifier.presented
    }
}
