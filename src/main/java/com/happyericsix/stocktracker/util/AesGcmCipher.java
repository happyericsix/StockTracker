package com.happyericsix.stocktracker.util;

/**
 * AES/GCM 加解密工具，用于保护同花顺会话凭证。
 *
 * <p>密文格式：{@code base64( iv(12字节) + ciphertext + gcmTag(16字节) )}
 *
 * <p>为什么用 GCM 而不是 CBC：
 * <ul>
 *   <li>GCM 自带完整性校验（认证标签）—— 密文被篡改会解密失败，而不是解出垃圾</li>
 *   <li>不需要额外处理 padding</li>
 * </ul>
 *
 * <p>密钥派生：把配置里的任意长度字符串用 <b>SHA-256</b> 摘要成 32 字节，
 * 而不是直接当密钥用。好处是配置里写多长都行（教程里给的
 * {@code dev-only-key-change-me-0000} 只有 27 字符，直接当 AES 密钥会报
 * "Invalid AES key length"）。<b>不要</b>在生产环境用弱密钥，但派生方式本身是安全的。
 */
public final class AesGcmCipher {

    /** GCM 推荐的 IV 长度（NIST SP 800-38D）。 */
    private static final int IV_LENGTH = 12;
    /** GCM 认证标签长度（bit）。 */
    private static final int TAG_LENGTH_BITS = 128;

    private AesGcmCipher() {
        // 工具类，不实例化
    }

    /**
     * 加密。
     *
     * @param plaintext 明文
     * @param keySource 配置里的密钥字符串（任意长度）
     * @return base64(iv + ciphertext + tag)
     */
    public static String encrypt(String plaintext, String keySource) {
        try {
            byte[] key = deriveKey(keySource);
            byte[] iv = new byte[IV_LENGTH];
            // 每次加密都用新的随机 IV —— 绝不能复用，否则 GCM 会彻底失去安全性
            new java.security.SecureRandom().nextBytes(iv);

            javax.crypto.Cipher cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding");
            javax.crypto.spec.GCMParameterSpec spec =
                    new javax.crypto.spec.GCMParameterSpec(TAG_LENGTH_BITS, iv);
            cipher.init(javax.crypto.Cipher.ENCRYPT_MODE,
                    new javax.crypto.spec.SecretKeySpec(key, "AES"), spec);

            byte[] ct = cipher.doFinal(plaintext.getBytes(java.nio.charset.StandardCharsets.UTF_8));

            byte[] out = new byte[iv.length + ct.length];
            System.arraycopy(iv, 0, out, 0, iv.length);
            System.arraycopy(ct, 0, out, iv.length, ct.length);
            return java.util.Base64.getEncoder().encodeToString(out);
        } catch (Exception e) {
            // 加密失败属于配置/环境问题（比如 JCE 策略限制），不该静默
            throw new IllegalStateException("凭证加密失败: " + e.getMessage(), e);
        }
    }

    /**
     * 解密。
     *
     * @param encrypted base64(iv + ciphertext + tag)
     * @param keySource 配置里的密钥字符串（必须和加密时一致）
     * @return 明文
     * @throws IllegalStateException 密钥不对或密文被篡改时抛出
     */
    public static String decrypt(String encrypted, String keySource) {
        try {
            byte[] all = java.util.Base64.getDecoder().decode(encrypted);
            if (all.length <= IV_LENGTH) {
                throw new IllegalArgumentException("密文长度不合法");
            }

            byte[] iv = java.util.Arrays.copyOfRange(all, 0, IV_LENGTH);
            byte[] ct = java.util.Arrays.copyOfRange(all, IV_LENGTH, all.length);

            javax.crypto.Cipher cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding");
            javax.crypto.spec.GCMParameterSpec spec =
                    new javax.crypto.spec.GCMParameterSpec(TAG_LENGTH_BITS, iv);
            cipher.init(javax.crypto.Cipher.DECRYPT_MODE,
                    new javax.crypto.spec.SecretKeySpec(deriveKey(keySource), "AES"), spec);

            return new String(cipher.doFinal(ct), java.nio.charset.StandardCharsets.UTF_8);
        } catch (Exception e) {
            // 最常见的原因：ths.aes.key 被改过（换了密钥就解不开旧密文）
            throw new IllegalStateException(
                    "凭证解密失败（ths.aes.key 是否被改过？）: " + e.getMessage(), e);
        }
    }

    /** 把任意长度的配置字符串摘要成 32 字节 AES-256 密钥。 */
    private static byte[] deriveKey(String keySource) {
        if (keySource == null || keySource.isBlank()) {
            throw new IllegalStateException("ths.aes.key 未配置，无法加解密同花顺凭证");
        }
        try {
            return java.security.MessageDigest.getInstance("SHA-256")
                    .digest(keySource.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        } catch (Exception e) {
            throw new IllegalStateException("派生密钥失败", e);
        }
    }
}
