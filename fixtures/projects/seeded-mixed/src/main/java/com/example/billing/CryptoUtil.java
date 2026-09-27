package com.example.billing;

import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

public class CryptoUtil {

    public byte[] encrypt(byte[] data) throws Exception {
        SecretKeySpec key = new SecretKeySpec("0123456789abcdef".getBytes(), "AES");
        byte[] iv = new byte[] {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
        IvParameterSpec spec = new IvParameterSpec(iv);
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.ENCRYPT_MODE, key, spec);
        return cipher.doFinal(data);
    }

    @SuppressWarnings("PMD.SystemPrintln")
    public void audit(String message) {
        System.out.println(message); // NOPMD - source-level suppressions do not change platform rules
    }
}
