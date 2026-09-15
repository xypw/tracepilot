package demo;

public class OrderStatusTest {
    public static void main(String[] args) {
        assert "UNKNOWN".equals(new OrderStatus().normalize(null)) : "null status should be unknown";
    }
}
