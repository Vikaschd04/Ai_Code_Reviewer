package shop;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;

public class CustomerRepository {
    private final Connection connection;

    public CustomerRepository(Connection connection) {
        this.connection = connection;
    }

    public ResultSet findByEmail(String email) throws Exception {
        Statement statement = connection.createStatement();
        return statement.executeQuery(
            "SELECT * FROM customers WHERE email = '" + email + "'");
    }
}
