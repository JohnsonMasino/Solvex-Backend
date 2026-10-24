from decimal import Decimal

from rest_framework import serializers

from .calculations import compute_state
from .models import Loan, LoanDuration, LoanRepayment, LoanStatus


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class LoanCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01"),
        error_messages={"min_value": "Amount must be greater than 0."},
    )
    duration = serializers.ChoiceField(choices=LoanDuration.choices)


class LoanRepaySerializer(serializers.Serializer):
    # Optional. Omit to pay as much as possible (min of available balance and amount owed).
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01"), required=False,
        error_messages={"min_value": "Amount must be greater than 0."},
    )


class LoanFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=LoanStatus.choices, required=False)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class LoanRepaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanRepayment
        fields = ("id", "amount", "source", "outstanding_after", "created_at")
        read_only_fields = fields


def _money(source):
    return serializers.DecimalField(max_digits=18, decimal_places=2, source=source, read_only=True)


class LoanSerializer(serializers.ModelSerializer):
    """
    status            pending | active | repaid | rejected | cancelled
    principal         amount requested / borrowed (never changes)
    outstanding       what the user still owes RIGHT NOW (principal + interest - repaid). Only non-zero for
                      an ACTIVE loan. Show it as "Loan amount" on the loan page, `principal` under "Loan details".
    interest_accrued  interest charged so far (the clock starts when the admin approves)
    total_due         principal + interest_accrued
    amount_repaid     everything paid so far
    started_at / halfway_at / due_at   null until the loan is approved
    decline_reason    only set when status is `rejected`
    """

    requested_at = serializers.DateTimeField(source="created_at", read_only=True)
    duration_label = serializers.CharField(source="get_duration_display", read_only=True)
    current_rate_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, source="state.current_rate_percent", read_only=True
    )
    daily_interest_usd = _money("state.daily_interest_usd")
    days_elapsed = serializers.IntegerField(source="state.days_elapsed", read_only=True)
    days_remaining = serializers.IntegerField(source="state.days_remaining", read_only=True)
    overdue_days = serializers.IntegerField(source="state.overdue_days", read_only=True)
    is_overdue = serializers.BooleanField(source="state.is_overdue", read_only=True)
    interest_accrued = _money("state.interest_accrued")
    total_due = _money("state.total_due")
    amount_repaid = _money("state.amount_repaid")
    outstanding = _money("state.outstanding")

    class Meta:
        model = Loan
        fields = (
            "id", "reference", "status", "principal",
            "duration", "duration_label", "duration_days",
            "daily_rate_percent", "overdue_rate_percent", "current_rate_percent", "daily_interest_usd",
            "requested_at", "processed_at", "started_at", "halfway_at", "due_at", "closed_at",
            "days_elapsed", "days_remaining", "overdue_days", "is_overdue",
            "interest_accrued", "total_due", "amount_repaid", "outstanding",
            "credited_to_wallet", "decline_reason",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        instance.state = compute_state(instance)
        data = super().to_representation(instance)
        if instance.status != LoanStatus.REJECTED:
            data["decline_reason"] = None
        return data


class LoanDetailSerializer(LoanSerializer):
    repayments = LoanRepaymentSerializer(many=True, read_only=True)

    class Meta(LoanSerializer.Meta):
        fields = LoanSerializer.Meta.fields + ("repayments",)
        read_only_fields = fields