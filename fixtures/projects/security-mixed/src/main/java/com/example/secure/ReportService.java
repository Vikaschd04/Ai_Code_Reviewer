package com.example.secure;

import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;

public final class ReportService {

    public ResultSet findByName(Connection connection, String name) throws Exception {
        Statement statement = connection.createStatement();
        return statement.executeQuery("SELECT * FROM report WHERE name = '" + name + "'");
    }

    public Process export(String format) throws Exception {
        return Runtime.getRuntime().exec("render-report --format " + format);
    }

    public byte[] checksum(byte[] data) throws Exception {
        return MessageDigest.getInstance("MD5").digest(data);
    }

    public byte[] seal(byte[] data) throws Exception {
        Cipher cipher = Cipher.getInstance("AES");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec("fixture-only-key".getBytes(), "AES"));
        return cipher.doFinal(data);
    }
}
