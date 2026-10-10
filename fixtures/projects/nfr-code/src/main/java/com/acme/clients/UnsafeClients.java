package com.acme.clients;

import java.net.HttpURLConnection;
import java.net.URL;
import java.net.http.HttpClient;
import org.springframework.web.client.RestTemplate;

public class UnsafeClients {
    private final RestTemplate rest = new RestTemplate();
    private final HttpClient http = HttpClient.newHttpClient();
    private final HttpClient pooled = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NORMAL).build();

    public int status(String address) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        connection.setRequestMethod("GET");
        return connection.getResponseCode();
    }
}
