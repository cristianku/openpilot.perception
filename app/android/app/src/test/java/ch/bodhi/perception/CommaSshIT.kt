package ch.bodhi.perception

import kotlinx.coroutines.runBlocking
import com.hierynomus.sshj.userauth.keyprovider.OpenSSHKeyV1KeyFile
import org.bouncycastle.asn1.ASN1OctetString
import org.bouncycastle.asn1.pkcs.PrivateKeyInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Against a real comma, only when asked: the SSH code of the app (host key pinning, the Ed25519 login from a raw
 * seed, the listener started from its standard input, its lines, the stop by closing the channel). Read-only on the
 * comma: the listener reads, and one pgrep checks it is gone. Each run takes one carState reader slot until the next
 * ignition (app/comma_listener.py): run it by hand, not in a loop.
 *
 *   PERCEPTION_SSH_IT_HOST=192.168.x.y PERCEPTION_SSH_IT_KEY=~/.ssh/id_ed25519 \
 *   PERCEPTION_SSH_IT_FINGERPRINT=SHA256:… gradle :app:testDebugUnitTest --tests '*CommaSshIT*'
 */
class CommaSshIT {
    private val host = System.getenv("PERCEPTION_SSH_IT_HOST").orEmpty()
    private val keyPath = System.getenv("PERCEPTION_SSH_IT_KEY").orEmpty()
    private val fingerprint = System.getenv("PERCEPTION_SSH_IT_FINGERPRINT").orEmpty()

    /** The 32-byte seed of an OpenSSH Ed25519 key file (never printed). */
    private fun seedOf(path: String): ByteArray {
        CommaSsh.installCrypto()
        val file = OpenSSHKeyV1KeyFile()
        file.init(File(path.replaceFirst("~", System.getProperty("user.home"))))
        val info = PrivateKeyInfo.getInstance(file.private.encoded)
        return ASN1OctetString.getInstance(info.parsePrivateKey()).octets
    }

    @Test fun listenerOnTheRealComma() {
        assumeTrue("set PERCEPTION_SSH_IT_HOST, _KEY and _FINGERPRINT to run it", host.isNotEmpty() && keyPath.isNotEmpty() && fingerprint.isNotEmpty())
        val seed = seedOf(keyPath)

        // The key exchange alone gives the comma's host key: the fingerprint the site pins.
        assertEquals(fingerprint, runBlocking { SshCommaConnector().hostKey(host, 22, null) })

        // Another pinned key: refused before any login, with the key it showed.
        try {
            CommaShell.open(host, null, setOf("SHA256:bbXpuKG6zhzdmnxq256TlqzFBzRl2f6OOg722cYNbU8"), seed.copyOf())
            throw AssertionError("a host key that is not pinned was accepted")
        } catch (refused: SshProblem.HostKeyRefused) {
            assertEquals(fingerprint, refused.presented)
        }

        val script = File(System.getProperty("perception.listener")!!).readBytes()
        val lines = java.util.Collections.synchronizedList(mutableListOf<String>())
        val enough = CountDownLatch(3)
        CommaShell.open(host, null, setOf(fingerprint), seed.copyOf()).use { shell ->
            val started = System.nanoTime()
            val channel = shell.startListener(ListenerProtocol.command("/data/openpilot", "/usr/local/venv/bin/python"), script)
            val reader = Thread {
                try {
                    channel.readLines { line ->
                        lines += line
                        enough.countDown()
                    }
                } catch (_: Exception) {
                }
            }
            reader.start()
            assertTrue("no 3 lines in 30 s: ${channel.errorText()}", enough.await(30, TimeUnit.SECONDS))
            val firstLineS = (System.nanoTime() - started) / 1e9
            channel.close()
            reader.join(5_000)
            val parsed = lines.mapNotNull(ListenerProtocol::parse)
            val hello = parsed.first() as ListenerLine.Hello
            assertEquals(ListenerProtocol.VERSION, hello.version)
            assertTrue(parsed.drop(1).all { it is ListenerLine.Status || it is ListenerLine.Radar })
            System.out.println("comma listener: ${lines.size} lines, the third after %.1f s; hello %s".format(firstLineS, hello))
            parsed.drop(1).forEach { System.out.println("  $it") }
        }
        // Closed: the listener's next write fails and it exits (about 1 s offroad).
        Thread.sleep(3_000)
        CommaShell.open(host, null, setOf(fingerprint), seed.copyOf()).use { shell ->
            val left = shell.run("pgrep -fa 'venv/bin/pytho[n] -B -u -$' || true").trim()
            assertEquals("a listener is still running on the comma", "", left)
        }
        seed.fill(0)
    }
}
