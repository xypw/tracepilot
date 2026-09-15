package demo;

public class OrderTotalTest {
    public static void main(String[] args) {
        long actual = new OrderTotal().calculate(50_000, 50_000);
        assert actual == 2_500_000_000L : "large total should not overflow";
    }
}
