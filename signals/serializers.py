from rest_framework import serializers

from .models import PurchasedEmail


class EligibilityDataSerializer(serializers.Serializer):
    eligible = serializers.BooleanField(help_text="true = open the page, false = show `message` and go to dashboard.")
    message = serializers.CharField(help_text="User-facing text. Safe to show in a toast / modal as-is.")


class EligibilityEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = EligibilityDataSerializer()


class PurchasedEmailSerializer(serializers.ModelSerializer):
    purchased_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = PurchasedEmail
        fields = ["email", "purchased_at"]
        read_only_fields = fields


class PurchasesDataSerializer(serializers.Serializer):
    count = serializers.IntegerField(help_text="Number of active purchased emails.")
    purchased = serializers.BooleanField(
        help_text="true if the LOGGED-IN user's own email is on the purchased list."
    )
    emails = PurchasedEmailSerializer(many=True)


class PurchasesEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = PurchasesDataSerializer()