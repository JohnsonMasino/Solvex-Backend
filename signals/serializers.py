from rest_framework import serializers


class EligibilityDataSerializer(serializers.Serializer):
    eligible = serializers.BooleanField(help_text="true = open the page, false = show `message` and go to dashboard.")
    message = serializers.CharField(help_text="User-facing text. Safe to show in a toast / modal as-is.")


class EligibilityEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = EligibilityDataSerializer()