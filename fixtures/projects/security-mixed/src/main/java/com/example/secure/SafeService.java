package com.example.secure;

import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import javax.crypto.Cipher;

public final class SafeService {

    public ResultSet findByName(Connection connection, String name) throws Exception {
        PreparedStatement statement = connection.prepareStatement("SELECT * FROM report WHERE name = ?");
        statement.setString(1, name);
        return statement.executeQuery();
    }

    public Process version() throws Exception {
        return Runtime.getRuntime().exec("render-report --version");
    }

    public byte[] checksum(byte[] data) throws Exception {
        return MessageDigest.getInstance("SHA-256").digest(data);
    }

    public Cipher cipher() throws Exception {
        return Cipher.getInstance("AES/GCM/NoPadding");
    }
}
