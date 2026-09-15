package demo;

public class TicketServiceTest {
    public static void main(String[] args) {
        assert "medium".equals(new TicketService().normalize(null));
    }
}

