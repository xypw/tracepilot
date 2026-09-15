package demo;

public class ApprovalServiceTest {
    public static void main(String[] args) {
        assert new ApprovalService().isApproved("APPROVED") : "status should ignore case";
    }
}
