package demo;

public class ReturnEligibility {
    public boolean canReturn(boolean delivered, int daysSinceDelivery) {
        return delivered || daysSinceDelivery <= 7;
    }
}
