import os
import unittest
from unittest.mock import patch


os.environ.setdefault(
    "POSTGRES_CONNECTION_STRING",
    "postgres://postgres:87087087@31.97.252.6:2024/projeto_queiroz?sslmode=disable",
)
os.environ.setdefault(
    "SUPERMERCADO_BASE_URL",
    "https://wildhub-aimerc-sistema.5mos1l.easypanel.host/api",
)
os.environ.setdefault("SUPERMERCADO_AUTH_TOKEN", "Bearer test")

from agent import (  # noqa: E402
    FLOW_AWAITING_ADDRESS_CONFIRMATION,
    FLOW_AWAITING_PAYMENT,
    FLOW_BUILDING,
    _advance_order_flow_state,
    _build_order_flow_directive,
    _sanitize_out_of_context_followups,
)
from tools import redis_tools  # noqa: E402


class FakeRedisClient:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


class OrderFlowRegressionTests(unittest.TestCase):
    def test_close_intent_advances_to_address_confirmation(self):
        stage = _advance_order_flow_state(FLOW_BUILDING, "so isso", True, True)
        self.assertEqual(stage, FLOW_AWAITING_ADDRESS_CONFIRMATION)

    def test_address_confirmation_advances_to_payment(self):
        stage = _advance_order_flow_state(
            FLOW_AWAITING_ADDRESS_CONFIRMATION,
            "sim",
            True,
            True,
        )
        self.assertEqual(stage, FLOW_AWAITING_PAYMENT)

    def test_new_item_during_payment_returns_to_building(self):
        stage = _advance_order_flow_state(
            FLOW_AWAITING_PAYMENT,
            "mais 2 arroz",
            True,
            True,
        )
        self.assertEqual(stage, FLOW_BUILDING)

    def test_payment_directive_requests_real_finalization(self):
        directive = _build_order_flow_directive(
            FLOW_AWAITING_PAYMENT,
            "dinheiro",
            True,
            "Samuel",
            "Rua X, Centro",
        )
        self.assertIn("finalizar_pedido_atual_tool", directive)
        self.assertIn("Nao pergunte novamente endereco ou pagamento", directive)

    def test_sanitize_final_response_removes_generic_followups(self):
        response = (
            "Seu pedido foi finalizado com sucesso!\n"
            "\n"
            "Como posso te ajudar hoje?\n"
            "Qual sera a forma de pagamento?"
        )
        clean = _sanitize_out_of_context_followups(response)
        self.assertEqual(clean, "Seu pedido foi finalizado com sucesso!")

    def test_post_checkout_followup_detection(self):
        self.assertTrue(redis_tools._is_post_checkout_followup_message("dinheiro"))
        self.assertTrue(redis_tools._is_post_checkout_followup_message("+"))
        self.assertTrue(redis_tools._is_post_checkout_followup_message("so isso"))
        self.assertFalse(redis_tools._is_post_checkout_followup_message("oi"))
        self.assertFalse(redis_tools._is_post_checkout_followup_message("2 arroz"))

    def test_completed_order_followup_does_not_open_new_order(self):
        fake_client = FakeRedisClient({"order_completed:5585": "1"})

        with patch.object(redis_tools, "get_redis_client", return_value=fake_client), patch.object(
            redis_tools, "get_order_session", return_value=None
        ), patch.object(redis_tools, "start_order_session") as start_session:
            ctx = redis_tools.get_order_context("5585", "dinheiro")

        self.assertIn("Pedido já finalizado recentemente", ctx)
        start_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
