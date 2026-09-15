package demo;

public class ReturnPolicy {
    public boolean isEligible(int daysSinceDelivery) {
        return daysSinceDelivery < 7;
    }
}

