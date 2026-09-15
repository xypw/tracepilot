package demo;

public class ApprovalService {
    public boolean isApproved(String status) {
        return "approved".equals(status);
    }
}
