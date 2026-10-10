package com.acme.clients;

import java.net.HttpURLConnection;
import java.net.URL;
import java.net.http.HttpClient;
import java.time.Duration;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

public class SafeClients {
    private final RestTemplate rest;
    private final RestTemplate built;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();

    public SafeClients(RestTemplateBuilder builder) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(2000);
        factory.setReadTimeout(5000);
        this.rest = new RestTemplate(factory);
        this.built = builder.connectTimeout(Duration.ofSeconds(2)).readTimeout(Duration.ofSeconds(5)).build();
    }

    public int status(String address) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        connection.setConnectTimeout(2000);
        connection.setReadTimeout(5000);
        return connection.getResponseCode();
    }
}
