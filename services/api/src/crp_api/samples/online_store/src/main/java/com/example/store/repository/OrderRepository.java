package com.example.store.repository;

import com.example.store.model.Order;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

public class OrderRepository {

    private final Connection connection;

    public OrderRepository(Connection connection) {
        this.connection = connection;
    }

    public List<Long> findOrderIdsByCustomer(String customerName) throws SQLException {
        Statement statement = connection.createStatement();
        ResultSet rows = statement.executeQuery(
                "SELECT id FROM orders WHERE customer_name = '" + customerName + "'");
        List<Long> ids = new ArrayList<>();
        while (rows.next()) {
            ids.add(rows.getLong("id"));
        }
        return ids;
    }

    public List<Order> recentOrders() {
        return null;
    }
}
