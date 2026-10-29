from rest_framework import serializers

from core import images
from core.exceptions import BusinessError

from . import services
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
# Image field check
# --------------------------------------------------------------------------- #
def _check_image(file):
    """
    Checks the REAL file content (type, size) so the error is attached to the right field,
    e.g. {"id_front": ["The image is too large. The maximum size is 5 MB."]}.
    The file is rewound afterwards because the service reads it again before uploading.
    """
    try:
        file.seek(0)
        images.read_and_validate(file)
    except BusinessError as exc:
        raise serializers.ValidationError(getattr(exc, "message", None) or str(exc))
    finally:
        try:
            file.seek(0)
        except Exception:
            pass
    return file


def _image_field(label):
    return serializers.FileField(
        allow_empty_file=False,
        error_messages={
            "required": f"Please upload {label}.",
            "null": f"Please upload {label}.",
            "empty": "The selected file is empty.",
            "invalid": "Upload the image as a file (multipart/form-data).",
            "no_name": "The selected file has no name.",
        },
    )


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
    """
    Sent as multipart/form-data. The five sections use dotted keys (personal.full_name,
    address.line1, ...); the two ID images are top-level file fields.
    The profile picture is NOT part of KYC (see the profiles app: POST /profile/photo/).
    """
    personal = PersonalSerializer()
    address = AddressSerializer()
    identification = IdentificationSerializer()
    employment = EmploymentSerializer()
    consent = ConsentSerializer()

    id_front = _image_field("the front of your ID document")
    id_back = _image_field("the back of your ID document")

    def validate_id_front(self, value):
        return _check_image(value)

    def validate_id_back(self, value):
        return _check_image(value)

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
    images = serializers.SerializerMethodField()

    class Meta(SubmissionSummarySerializer.Meta):
        fields = SubmissionSummarySerializer.Meta.fields + ("id_type", "id_number", "images")
        read_only_fields = fields

    def get_images(self, obj):
        return services.get_submission_images(obj)


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
            "images": services.get_submission_images(s),
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