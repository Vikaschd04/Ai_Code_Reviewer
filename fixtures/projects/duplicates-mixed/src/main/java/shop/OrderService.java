package shop;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;

public class OrderService {
    public BigDecimal total(List<BigDecimal> amounts, int quantity, String coupon) {
        BigDecimal subtotal = BigDecimal.ZERO;
        for (BigDecimal amount : amounts) {
            if (amount.compareTo(BigDecimal.valueOf(100)) > 0) {
                subtotal = subtotal.add(amount.multiply(BigDecimal.valueOf(0.95)));
            } else {
                subtotal = subtotal.add(amount);
            }
        }
        if (quantity >= 10) {
            subtotal = subtotal.multiply(BigDecimal.valueOf(0.9));
        }
        if (coupon != null && coupon.startsWith("SAVE")) {
            int percent = Integer.parseInt(coupon.substring(4));
            subtotal = subtotal.subtract(subtotal.multiply(BigDecimal.valueOf(percent)).divide(BigDecimal.valueOf(100)));
        }
        BigDecimal tax = subtotal.multiply(BigDecimal.valueOf(0.2));
        return subtotal.add(tax).setScale(2, RoundingMode.HALF_UP);
    }
}
