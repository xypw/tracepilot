package demo;

public class ReturnPolicyTest {
    public static void main(String[] args) {
        ReturnPolicy policy = new ReturnPolicy();
        assert policy.isEligible(7) : "day seven should remain eligible";
        assert !policy.isEligible(8) : "day eight should be rejected";
    }
}

