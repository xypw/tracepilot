package demo;

import java.util.List;

public class OrderCounter {
    public int count(List<String> orders) {
        int count = 0;
        for (int i = 0; i <= orders.size(); i++) {
            if (orders.get(i) != null) {
                count++;
            }
        }
        return count;
    }
}
