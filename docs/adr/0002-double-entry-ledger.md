# ADR-0002: Ledger kế toán kép, bất biến, có balance projection

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Bối cảnh

Một cột `balance` cập nhật tại chỗ không giải thích được vì sao số dư là như vậy, khiến đối soát không thể thực hiện và dễ hỏng dữ liệu âm thầm. Yêu cầu đòi hỏi bản ghi bất biến, sửa sai có liên kết và cơ chế kiểm soát toàn vẹn.

## Các phương án

1. Cột số dư + bảng giao dịch (đơn giản, audit yếu).
2. Bảng biến động bút toán đơn, số dư = tổng (tốt hơn, nhưng không có kiểm tra cân bằng kế toán).
3. **Ledger kế toán kép** (journal + posting, nợ = có) với bảng balance projection cập nhật trong cùng DB transaction.

## Quyết định

Phương án 3. Hệ thống tài khoản: CUSTOMER_WALLET (nợ phải trả), HOLD, PROVIDER_SETTLEMENT (tài sản), FEE_REVENUE, ADJUSTMENT_EXPENSE, SUSPENSE. Journal và posting là chỉ-thêm (trigger + quyền DB). `account_balances` là projection, cập nhật nguyên tử cùng posting và được đối soát kiểm tra. Khoản giữ (rút tiền đang chờ, review rủi ro) là posting vào HOLD, không phải cột phụ.

## Hệ quả

- Mọi số dư giải thích và đối soát được; sửa sai bằng journal mới.
- Nhiều bảng và quy tắc posting hơn cần thiết kế và test (giảm thiểu bằng posting builder thuần domain và property-based test).
- Đọc số dư O(1) qua projection; lịch sử lấy từ posting.
