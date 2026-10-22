from rest_framework import serializers

from . import validators as v
from .models import (
    AddressEntryMethod, AnnualIncomeRange, EmploymentStatus, ExpectedAnnualVolume,
    IDType, KYCStatus, KYCSubmission, Sex, SourceOfIncome, TransactionFrequency,
    TypicalTransactionSize,
)


# --------------------------------------------------------------------------- #
# Error flattening: nested errors -> {"personal.date_of_birth": ["..."]}
# --------------------------------------------------------------------------- #
def _flatten_errors(detail, prefix=""):
    out = {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten_errors(value, child))
    elif isinstance(detail, (list, tuple)):
        messages = [str(i) for i in detail if not isinstance(i, (dict, list, tuple))]
        if messages:
            out[prefix or "non_field_errors"] = messages
        for item in detail:
            if isinstance(item, (dict, list, tuple)):
                out.update(_flatten_errors(item, prefix))
    else:
        out[prefix or "non_field_errors"] = [str(detail)]
    return out


# --------------------------------------------------------------------------- #
# Request serializers (one per section)
# --------------------------------------------------------------------------- #
class PersonalSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    date_of_birth = serializers.DateField()
    nationality = serializers.CharField(max_length=2)
    sex = serializers.ChoiceField(choices=Sex.choices)
    phone_number = serializers.CharField(max_length=30)

    def validate_full_name(self, value):
        return v.validate_person_name(value)

    def validate_date_of_birth(self, value):
        return v.validate_birth_date(value)

    def validate_nationality(self, value):
        return v.validate_country_code(value)

    def validate_phone_number(self, value):
        return v.validate_phone(value)


class AddressSerializer(serializers.Serializer):
    line1 = serializers.CharField(max_length=255)
    line2 = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=2)
    entry_method = serializers.ChoiceField(choices=AddressEntryMethod.choices)

    def validate_country(self, value):
        return v.validate_country_code(value)


class IdentificationSerializer(serializers.Serializer):
    id_type = serializers.ChoiceField(choices=IDType.choices)
    id_number = serializers.CharField(max_length=60)

    def validate_id_number(self, value):
        return v.validate_id_number(value)


class EmploymentSerializer(serializers.Serializer):
    employment_status = serializers.ChoiceField(choices=EmploymentStatus.choices)
    source_of_income = serializers.ChoiceField(choices=SourceOfIncome.choices)
    occupation = serializers.CharField(max_length=120)
    employer_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    annual_income_range = serializers.ChoiceField(choices=AnnualIncomeRange.choices)
    expected_annual_volume = serializers.ChoiceField(choices=ExpectedAnnualVolume.choices)
    transaction_frequency = serializers.ChoiceField(choices=TransactionFrequency.choices)
    typical_transaction_size = serializers.ChoiceField(choices=TypicalTransactionSize.choices)

    def validate(self, attrs):
        if attrs["employment_status"] in (EmploymentStatus.EMPLOYED, EmploymentStatus.SELF_EMPLOYED):
            if not (attrs.get("employer_name") or "").strip():
                raise serializers.ValidationError(
                    {"employer_name": "Employer / business name is required for your employment status."}
                )
        return attrs


class ConsentSerializer(serializers.Serializer):
    accuracy_declaration = serializers.BooleanField()
    privacy_notice_accepted = serializers.BooleanField()
    terms_accepted = serializers.BooleanField()
    marketing_consent = serializers.BooleanField(required=False, default=False)

    @staticmethod
    def _must_be_true(value, message):
        if value is not True:
            raise serializers.ValidationError(message)
        return value

    def validate_accuracy_declaration(self, value):
        return self._must_be_true(value, "You must confirm that the information provided is accurate.")

    def validate_privacy_notice_accepted(self, value):
        return self._must_be_true(value, "You must accept the privacy notice.")

    def validate_terms_accepted(self, value):
        return self._must_be_true(value, "You must accept the terms and conditions.")


class SubmitKYCSerializer(serializers.Serializer):
    personal = PersonalSerializer()
    address = AddressSerializer()
    identification = IdentificationSerializer()
    employment = EmploymentSerializer()
    consent = ConsentSerializer()

    def run_validation(self, data=serializers.empty):
        try:
            return super().run_validation(data)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(_flatten_errors(exc.detail))


# --------------------------------------------------------------------------- #
# Response serializers
# --------------------------------------------------------------------------- #
class SubmissionSummarySerializer(serializers.ModelSerializer):
    submitted_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = KYCSubmission
        fields = ("id", "attempt_number", "status", "submitted_at", "reviewed_at", "rejection_reason")
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # The reason is only ever shown for rejected submissions.
        if instance.status != KYCStatus.REJECTED:
            data["rejection_reason"] = None
        return data


class SubmissionCreatedSerializer(SubmissionSummarySerializer):
    id_type = serializers.CharField(read_only=True)
    id_number = serializers.CharField(source="masked_id_number", read_only=True)

    class Meta(SubmissionSummarySerializer.Meta):
        fields = SubmissionSummarySerializer.Meta.fields + ("id_type", "id_number")
        read_only_fields = fields


class SubmissionDetailSerializer(serializers.BaseSerializer):
    """Full read-only view of a submission. Never exposes admin_note, reviewer, IP or user agent."""

    def to_representation(self, s):
        dt = serializers.DateTimeField()
        return {
            "id": str(s.id),
            "attempt_number": s.attempt_number,
            "status": s.status,
            "submitted_at": dt.to_representation(s.created_at),
            "reviewed_at": dt.to_representation(s.reviewed_at) if s.reviewed_at else None,
            "rejection_reason": s.rejection_reason if s.status == KYCStatus.REJECTED else None,
            "personal": {
                "full_name": s.full_name,
                "date_of_birth": s.date_of_birth.isoformat(),
                "nationality": s.nationality,
                "sex": s.sex,
                "phone_number": s.phone_number,
            },
            "address": {
                "line1": s.address_line1,
                "line2": s.address_line2,
                "city": s.city,
                "state": s.state,
                "postal_code": s.postal_code,
                "country": s.country,
                "entry_method": s.address_entry_method,
            },
            "identification": {"id_type": s.id_type, "id_number": s.masked_id_number},
            "employment": {
                "employment_status": s.employment_status,
                "source_of_income": s.source_of_income,
                "occupation": s.occupation,
                "employer_name": s.employer_name,
                "annual_income_range": s.annual_income_range,
                "expected_annual_volume": s.expected_annual_volume,
                "transaction_frequency": s.transaction_frequency,
                "typical_transaction_size": s.typical_transaction_size,
            },
            "consent": {
                "accuracy_declaration": s.accuracy_declaration,
                "privacy_notice_accepted": s.privacy_notice_accepted,
                "terms_accepted": s.terms_accepted,
                "marketing_consent": s.marketing_consent,
                "policy_version": s.policy_version,
                "consented_at": dt.to_representation(s.consent_at),
            },
        }
