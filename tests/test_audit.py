import json
import tempfile

from paygraph.audit import AuditLogger, AuditRecord


class TestAuditRecord:
    def test_now_sets_timestamp(self):
        record = AuditRecord.now(
            agent_id="test",
            amount=5.0,
            vendor="vendor",
            justification="reason",
            policy_result="approved",
        )
        assert record.timestamp is not None
        assert "T" in record.timestamp  # ISO 8601

    def test_now_defaults(self):
        record = AuditRecord.now(
            agent_id="test",
            amount=5.0,
            vendor="vendor",
            justification="reason",
            policy_result="denied",
        )
        assert record.gateway_ref is None
        assert record.gateway_type is None
        assert record.checks_passed == []


class TestAuditLogger:
    def test_appends_valid_jsonl(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        logger = AuditLogger(log_path=path, verbose=False)
        r1 = AuditRecord.now("a", 1.0, "v1", "j1", "approved", gateway_ref="tok_1")
        r2 = AuditRecord.now("a", 2.0, "v2", "j2", "denied", denial_reason="nope")
        logger.log(r1)
        logger.log(r2)

        with open(path) as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) == 2

        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["amount"] == 1.0
        assert parsed[0]["gateway_ref"] == "tok_1"
        assert parsed[1]["amount"] == 2.0
        assert parsed[1]["denial_reason"] == "nope"

    def test_each_line_is_valid_json(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        logger = AuditLogger(log_path=path, verbose=False)
        for i in range(5):
            logger.log(AuditRecord.now("a", float(i), "v", "j", "approved"))

        with open(path) as f:
            for line in f:
                if line.strip():
                    json.loads(line)  # should not raise

    def test_verbose_prints(self, capsys):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        logger = AuditLogger(log_path=path, verbose=True)

        # start_request prints the header with amount
        logger.start_request(5.0, "vendor")
        captured = capsys.readouterr()
        assert "$5.00" in captured.out
        assert "vendor" in captured.out

        # log prints the result
        logger.log(
            AuditRecord.now("a", 5.0, "vendor", "reason", "approved", gateway_ref="t")
        )
        captured = capsys.readouterr()
        assert "APPROVED" in captured.out

    def test_verbose_denied_shows_reason(self, capsys):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        logger = AuditLogger(log_path=path, verbose=True)
        logger.log(
            AuditRecord.now("a", 5.0, "v", "j", "denied", denial_reason="too much")
        )
        captured = capsys.readouterr()
        assert "DENIED" in captured.out
        assert "too much" in captured.out
