package demo;

import java.math.BigDecimal;

public class AmountMatcher {
    public boolean sameAmount(BigDecimal expected, BigDecimal actual) {
        return expected.equals(actual);
    }
}

