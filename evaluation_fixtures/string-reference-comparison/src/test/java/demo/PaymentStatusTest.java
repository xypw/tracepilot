package demo;

public class PaymentStatusTest {
    public static void main(String[] args) {
        String status = new String("PAID");
        assert new PaymentStatus().isPaid(status) : "dynamically loaded PAID status should match";
    }
}

