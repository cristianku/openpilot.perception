# R8 rules of the release build.
# Keep line numbers in crash reports.
-keepattributes SourceFile,LineNumberTable,Signature,InnerClasses,EnclosingMethod
-renamesourcefileattribute SourceFile

# Bouncy Castle is a JCA provider: it names its algorithm classes in strings and loads them by reflection, so
# R8 cannot see them. The provider classes are kept whole; the plain cryptography they use is kept through them.
-keep class org.bouncycastle.jcajce.provider.** { *; }
-keep class org.bouncycastle.jce.provider.** { *; }
-keep class org.bouncycastle.pqc.jcajce.provider.** { *; }
-keep class org.bouncycastle.crypto.CryptoServicesRegistrar { *; }
-dontwarn javax.naming.**
-dontwarn org.bouncycastle.jsse.**
-dontwarn org.bouncycastle.mime.**

# sshj: algorithm factories are plain classes, but some are picked by name; keep the library as it is, it is small.
-keep class net.schmizz.sshj.** { *; }
-keep class com.hierynomus.sshj.** { *; }
-keep class com.hierynomus.asn1.** { *; }
-dontwarn org.slf4j.**
-dontwarn javax.security.auth.**
-dontwarn org.ietf.jgss.**
-dontwarn net.i2p.crypto.eddsa.**
-dontwarn com.jcraft.jzlib.**
