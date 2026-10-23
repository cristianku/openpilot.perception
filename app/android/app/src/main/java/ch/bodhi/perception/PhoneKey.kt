package ch.bodhi.perception

import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/** Ed25519 keys in the OpenSSH forms the comma and GitHub take. */
object SshKeys {
    const val TYPE = "ssh-ed25519"

    /** A new private key: its 32-byte seed. */
    fun newSeed(random: SecureRandom = SecureRandom()): ByteArray = Ed25519PrivateKeyParameters(random).encoded

    /** The 32-byte public key of a seed. */
    fun publicOf(seed: ByteArray): ByteArray {
        require(seed.size == 32) { "an Ed25519 seed has 32 bytes" }
        return Ed25519PrivateKeyParameters(seed, 0).generatePublicKey().encoded
    }

    /** The SSH wire form of a public key: string "ssh-ed25519", string key. */
    fun blob(publicKey: ByteArray): ByteArray {
        val bytes = ByteArrayOutputStream()
        DataOutputStream(bytes).use { out ->
            val type = TYPE.toByteArray(Charsets.US_ASCII)
            out.writeInt(type.size)
            out.write(type)
            out.writeInt(publicKey.size)
            out.write(publicKey)
        }
        return bytes.toByteArray()
    }

    /** "ssh-ed25519 AAAA… perception-app@<model>", one line of authorized_keys or of GitHub. */
    fun authorizedLine(publicKey: ByteArray, comment: String): String =
        "$TYPE ${Base64.getEncoder().encodeToString(blob(publicKey))} ${safeComment(comment)}"

    /** SHA256:… as `ssh-keygen -l` prints it. */
    fun fingerprint(blob: ByteArray): String =
        "SHA256:" + Base64.getEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256").digest(blob))

    /** perception-app@<model>, with only characters every authorized_keys and GitHub accept. */
    fun comment(model: String): String = "perception-app@" + model.replace(Regex("[^A-Za-z0-9._-]+"), "-").trim('-').take(40).ifEmpty { "android" }

    private fun safeComment(comment: String): String = comment.replace(Regex("[^A-Za-z0-9._@-]+"), "-").take(80)
}

/** Encrypts the private key at rest: an AES key of the Android Keystore in the app ([KeystoreWrapper]), a fake
 * in the tests. */
interface KeyWrapper {
    fun wrap(plain: ByteArray): ByteArray
    fun unwrap(wrapped: ByteArray): ByteArray
    fun forget()
}

/** The phone's own SSH key: made on the phone, the private key never leaves it (encrypted in the app's private
 * files with [wrapper]); the public key goes to the comma, by the site or by GitHub. */
class PhoneKeyStore(private val folder: File, private val wrapper: KeyWrapper) {
    private val privateFile get() = File(folder, "phone_key.enc")
    private val publicFile get() = File(folder, "phone_key.pub")

    /** The authorized_keys line, null before the key is made. */
    fun publicLine(): String? = try {
        publicFile.takeIf { it.isFile && privateFile.isFile }?.readText(Charsets.US_ASCII)?.trim()?.takeIf { it.startsWith(SshKeys.TYPE + " ") }
    } catch (_: IOException) {
        null
    }

    fun fingerprint(): String? = publicLine()?.split(' ')?.getOrNull(1)?.let {
        try {
            SshKeys.fingerprint(Base64.getDecoder().decode(it))
        } catch (_: IllegalArgumentException) {
            null
        }
    }

    /** Makes a key, replacing the old one (which the comma may still accept until it is removed there). */
    @Synchronized
    fun create(comment: String): String {
        val seed = SshKeys.newSeed()
        try {
            val line = SshKeys.authorizedLine(SshKeys.publicOf(seed), comment)
            folder.mkdirs()
            writeAtomically(privateFile, wrapper.wrap(seed))
            writeAtomically(publicFile, (line + "\n").toByteArray(Charsets.US_ASCII))
            return line
        } finally {
            seed.fill(0)
        }
    }

    fun ensure(comment: String): String = publicLine() ?: create(comment)

    /** The seed, decrypted for one SSH login; the caller wipes it after use. Null without a key. */
    @Synchronized
    fun seed(): ByteArray? {
        if (!privateFile.isFile) return null
        val seed = wrapper.unwrap(privateFile.readBytes())
        check(seed.size == 32) { "the phone key is damaged" }
        return seed
    }

    @Synchronized
    fun delete() {
        privateFile.delete()
        publicFile.delete()
        wrapper.forget()
    }

    private fun writeAtomically(target: File, bytes: ByteArray) {
        val part = File(target.parentFile, target.name + ".part")
        FileOutputStream(part).use {
            it.write(bytes)
            it.fd.sync()
        }
        if (!part.renameTo(target)) throw IOException("the phone key could not be saved")
    }
}

/** AES-256-GCM with a key that lives in the Android Keystore (hardware-backed where the phone has it) and can
 * never be read out; usable without unlocking prompts, so the radar works with the phone in a car holder.
 * Stored form: version byte 1, 12-byte IV, ciphertext and tag. */
class KeystoreWrapper(private val alias: String = "perception_phone_key") : KeyWrapper {
    private fun key(create: Boolean): javax.crypto.SecretKey? {
        val store = java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? javax.crypto.SecretKey)?.let { return it }
        if (!create) return null
        val generator = javax.crypto.KeyGenerator.getInstance(android.security.keystore.KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(android.security.keystore.KeyGenParameterSpec.Builder(alias,
            android.security.keystore.KeyProperties.PURPOSE_ENCRYPT or android.security.keystore.KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(android.security.keystore.KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(android.security.keystore.KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setRandomizedEncryptionRequired(true)
            .build())
        return generator.generateKey()
    }

    override fun wrap(plain: ByteArray): ByteArray {
        val cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(javax.crypto.Cipher.ENCRYPT_MODE, key(create = true))
        val iv = cipher.iv
        check(iv.size == 12)
        return byteArrayOf(1) + iv + cipher.doFinal(plain)
    }

    override fun unwrap(wrapped: ByteArray): ByteArray {
        check(wrapped.size > 13 && wrapped[0] == 1.toByte()) { "the phone key is damaged" }
        val cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(javax.crypto.Cipher.DECRYPT_MODE, key(create = false) ?: error("the key of the phone key is gone"),
            javax.crypto.spec.GCMParameterSpec(128, wrapped, 1, 12))
        return cipher.doFinal(wrapped, 13, wrapped.size - 13)
    }

    override fun forget() {
        try {
            java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
        } catch (_: Exception) {
        }
    }
}
