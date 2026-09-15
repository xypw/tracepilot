package demo;

public class RefundMetricsTest {
    public static void main(String[] args) {
        double actual = new RefundMetrics().ratio(1, 2);
        assert Math.abs(actual - 0.5) < 0.0001 : "expected refund ratio 0.5";
    }
}

