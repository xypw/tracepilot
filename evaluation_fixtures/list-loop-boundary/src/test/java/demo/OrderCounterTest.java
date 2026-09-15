package demo;

import java.util.List;

public class OrderCounterTest {
    public static void main(String[] args) {
        int actual = new OrderCounter().count(List.of("O-1", "O-2"));
        assert actual == 2 : "expected two orders";
    }
}
