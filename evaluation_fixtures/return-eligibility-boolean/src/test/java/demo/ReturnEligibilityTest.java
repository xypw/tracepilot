package demo;

public class ReturnEligibilityTest {
    public static void main(String[] args) {
        assert !new ReturnEligibility().canReturn(false, 3) : "undelivered order cannot return";
    }
}
