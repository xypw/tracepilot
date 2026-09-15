package demo;

import java.math.BigDecimal;

public class AmountMatcherTest {
    public static void main(String[] args) {
        AmountMatcher matcher = new AmountMatcher();
        assert matcher.sameAmount(new BigDecimal("10.0"), new BigDecimal("10.00"));
    }
}

