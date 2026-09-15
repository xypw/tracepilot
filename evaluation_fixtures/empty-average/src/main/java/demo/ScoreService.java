package demo;

import java.util.List;

public class ScoreService {
    public double average(List<Integer> values) {
        int total = 0;
        for (int value : values) {
            total += value;
        }
        return total / values.size();
    }
}
