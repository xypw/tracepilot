package demo;

public class CustomerServiceTest {
    public static void main(String[] args) {
        assert "anonymous".equals(new CustomerService().displayName(null));
    }
}

