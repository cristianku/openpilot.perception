package ch.bodhi.perception

import net.schmizz.sshj.common.Buffer
import net.schmizz.sshj.common.KeyType
import net.schmizz.sshj.common.SecurityUtils
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.Base64

class PhoneKeyTest {
    @get:Rule val folder = TemporaryFolder()

    /** RFC 8032, test 1: a published key that nobody uses. */
    private val seed = hex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    private val public = hex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")

    private fun hex(text: String) = ByteArray(text.length / 2) { text.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    /** No Android Keystore on the JVM: a reversible stand-in, enough to test the store around it. */
    private class XorWrapper : KeyWrapper {
        var forgotten = false
        override fun wrap(plain: ByteArray) = plain.map { (it.toInt() xor 0x5A).toByte() }.toByteArray()
        override fun unwrap(wrapped: ByteArray) = wrap(wrapped)
        override fun forget() {
            forgotten = true
        }
    }

    @Test fun theOpenSshFormsOfAKnownKey() {
        assertArrayEquals(public, SshKeys.publicOf(seed))
        val line = SshKeys.authorizedLine(public, SshKeys.comment("Pixel 8 Pro"))
        // As ssh-keygen writes it, and its fingerprint as ssh-keygen -l prints it.
        assertEquals("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3B1Ea perception-app@Pixel-8-Pro", line)
        assertEquals("SHA256:bbXpuKG6zhzdmnxq256TlqzFBzRl2f6OOg722cYNbU8", SshKeys.fingerprint(SshKeys.blob(public)))
        assertEquals("perception-app@android", SshKeys.comment("   "))
    }

    @Test fun sshjSignsWithThePhoneKey() {
        val provider = SeedKeyProvider(seed)
        assertEquals(KeyType.ED25519, KeyType.fromKey(provider.public))
        assertEquals(KeyType.ED25519, KeyType.fromKey(provider.private))
        // sshj writes the same public key the comma and GitHub get, and pins host keys with the same fingerprint.
        assertArrayEquals(SshKeys.blob(public), Buffer.PlainBuffer().putPublicKey(provider.public).compactData)
        assertEquals("SHA256:bbXpuKG6zhzdmnxq256TlqzFBzRl2f6OOg722cYNbU8", CommaSsh.fingerprint(provider.public))
        val signer = SecurityUtils.getSignature("Ed25519")
        signer.initSign(provider.private)
        signer.update("session".toByteArray())
        val signature = signer.sign()
        val verifier = SecurityUtils.getSignature("Ed25519")
        verifier.initVerify(provider.public)
        verifier.update("session".toByteArray())
        assertTrue(verifier.verify(signature))
        // RFC 8032 test 1 signs the empty message with this known signature.
        signer.initSign(provider.private)
        assertEquals("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
            signer.sign().joinToString("") { "%02x".format(it) })
    }

    @Test fun theStoreKeepsThePrivateKeyWrapped() {
        val wrapper = XorWrapper()
        val store = PhoneKeyStore(folder.root, wrapper)
        assertNull(store.publicLine())
        val line = store.create("perception-app@Pixel-8")
        assertEquals(line, store.publicLine())
        assertEquals(line, store.ensure("ignored"))
        val stored = folder.root.resolve("phone_key.enc").readBytes()
        val seed = store.seed()!!
        assertEquals(32, seed.size)
        assertNotEquals(seed.toList(), stored.toList())
        // The public line is the one of the stored seed.
        assertEquals(line, SshKeys.authorizedLine(SshKeys.publicOf(seed), "perception-app@Pixel-8"))
        val blob = Base64.getDecoder().decode(line.split(' ')[1])
        assertEquals(SshKeys.fingerprint(blob), store.fingerprint())
        val second = store.create("perception-app@Pixel-8")
        assertNotEquals(line, second)
        store.delete()
        assertNull(store.publicLine())
        assertNull(store.seed())
        assertTrue(wrapper.forgotten)
    }
}
