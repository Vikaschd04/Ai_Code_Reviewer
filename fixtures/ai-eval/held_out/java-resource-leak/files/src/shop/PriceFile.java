package shop;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;

public class PriceFile {
    public String firstLine(String path) throws IOException {
        BufferedReader reader = new BufferedReader(new FileReader(path));
        return reader.readLine();
    }
}
