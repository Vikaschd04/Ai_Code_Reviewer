package com.example.store.service;

import java.math.BigDecimal;
import java.security.MessageDigest;
import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;

public class PaymentService {

    public String charge(String email, BigDecimal amount) throws Exception {
        byte[] reference = MessageDigest.getInstance("MD5").digest(email.getBytes());
        return email + ":" + amount + ":" + reference.length;
    }

    public byte[] encryptCardNumber(byte[] cardNumber) throws Exception {
        SecretKeySpec key = new SecretKeySpec("0123456789abcdef".getBytes(), "AES");
        Cipher cipher = Cipher.getInstance("AES/ECB/PKCS5Padding");
        cipher.init(Cipher.ENCRYPT_MODE, key);
        return cipher.doFinal(cardNumber);
    }

    public Process printReceipt(String orderId) throws Exception {
        return Runtime.getRuntime().exec("print-receipt --order " + orderId);
    }
}
