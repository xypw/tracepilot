package demo;

import java.util.List;

public class ScoreServiceTest {
    public static void main(String[] args) {
        assert new ScoreService().average(List.of()) == 0.0 : "empty scores should return zero";
    }
}
