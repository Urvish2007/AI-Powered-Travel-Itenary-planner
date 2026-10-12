"""Unit tests for security, time handling, geometry and the LLM stub."""

from __future__ import annotations

import time

import jwt
import pytest

from app.ai.embeddings import cosine_similarity, hash_embed, tokenize
from app.ai.llm import StubProvider, extract_json
from app.ai.schemas import TripIntent
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.timeutils import overflows_day, to_clock, to_minutes
from app.models.base import GeoPoint


class TestPasswordHashing:
    def test_round_trip(self) -> None:
        digest = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", digest)

    def test_rejects_the_wrong_password(self) -> None:
        assert not verify_password("wrong", hash_password("right"))

    def test_hash_is_salted(self) -> None:
        """Two hashes of the same password must differ, or the salt is broken."""
        assert hash_password("same") != hash_password("same")

    def test_plaintext_never_appears_in_the_hash(self) -> None:
        secret = "supersecretvalue"
        assert secret not in hash_password(secret)

    def test_malformed_hash_is_rejected_rather_than_raising(self) -> None:
        assert not verify_password("anything", "not-a-valid-argon2-hash")

    def test_long_passwords_are_supported(self) -> None:
        """Argon2 has no 72-byte truncation, unlike bcrypt."""
        long_password = "x" * 200 + "tail"
        digest = hash_password(long_password)
        assert verify_password(long_password, digest)
        assert not verify_password("x" * 200 + "different", digest)


class TestTokens:
    def test_access_token_round_trip(self) -> None:
        token = create_access_token("user-123")
        payload = decode_token(token, "access")
        assert payload["sub"] == "user-123"
        assert payload["type"] == "access"

    def test_refresh_token_round_trip(self) -> None:
        payload = decode_token(create_refresh_token("user-123"), "refresh")
        assert payload["type"] == "refresh"

    def test_token_type_is_enforced(self) -> None:
        """An access token must not be usable where a refresh token is expected."""
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(create_access_token("u"), "refresh")

    def test_tampered_token_is_rejected(self) -> None:
        token = create_access_token("user-123")
        head, payload, signature = token.split(".")
        tampered = f"{head}.{payload}.{signature[:-4]}abcd"
        with pytest.raises(jwt.PyJWTError):
            decode_token(tampered)

    def test_token_signed_with_another_key_is_rejected(self) -> None:
        forged = jwt.encode(
            {"sub": "attacker", "type": "access", "exp": int(time.time()) + 600},
            "a-completely-different-signing-key-value",
            algorithm="HS256",
        )
        with pytest.raises(jwt.PyJWTError):
            decode_token(forged, "access")

    def test_expired_token_is_rejected(self) -> None:
        expired = jwt.encode(
            {"sub": "u", "type": "access", "exp": int(time.time()) - 60},
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(expired, "access")

    def test_each_token_has_a_unique_id(self) -> None:
        a = decode_token(create_refresh_token("u"), "refresh")["jti"]
        b = decode_token(create_refresh_token("u"), "refresh")["jti"]
        assert a != b


class TestTimeUtils:
    @pytest.mark.parametrize(
        ("clock", "minutes"),
        [("00:00", 0), ("09:30", 570), ("12:00", 720), ("23:59", 1439)],
    )
    def test_parsing(self, clock: str, minutes: int) -> None:
        assert to_minutes(clock) == minutes
        assert to_clock(minutes) == clock

    @pytest.mark.parametrize("bad", ["24:00", "25:10", "9:70", "not-a-time", ""])
    def test_rejects_invalid_clock_strings(self, bad: str) -> None:
        with pytest.raises(ValueError):
            to_minutes(bad)

    def test_to_clock_refuses_to_wrap_past_midnight(self) -> None:
        """Wrapping silently turns an overrun into an end-before-start bug."""
        with pytest.raises(ValueError):
            to_clock(1440)
        with pytest.raises(ValueError):
            to_clock(-1)

    def test_overflow_detection(self) -> None:
        assert not overflows_day(0)
        assert not overflows_day(1439)
        assert overflows_day(1440)
        assert overflows_day(-1)


class TestGeoPoint:
    def test_stores_coordinates_in_geojson_order(self) -> None:
        """GeoJSON is [lng, lat]; getting this backwards is the classic bug."""
        point = GeoPoint.from_lat_lng(48.8584, 2.2945)
        assert point.coordinates == [2.2945, 48.8584]
        assert point.latitude == 48.8584
        assert point.longitude == 2.2945

    def test_round_trip(self) -> None:
        assert GeoPoint.from_lat_lng(35.68, 139.76).as_lat_lng() == (35.68, 139.76)

    @pytest.mark.parametrize(
        "coordinates", [[181.0, 0.0], [-181.0, 0.0], [0.0, 91.0], [0.0, -91.0]]
    )
    def test_rejects_out_of_range_coordinates(self, coordinates: list[float]) -> None:
        with pytest.raises(ValueError):
            GeoPoint(coordinates=coordinates)

    def test_requires_exactly_two_coordinates(self) -> None:
        with pytest.raises(ValueError):
            GeoPoint(coordinates=[1.0])


class TestEmbeddings:
    def test_dimensions_match_configuration(self) -> None:
        assert len(hash_embed("a museum in paris")) == settings.embedding_dimensions

    def test_is_deterministic(self) -> None:
        assert hash_embed("same text") == hash_embed("same text")

    def test_is_normalised(self) -> None:
        vector = hash_embed("an art gallery with paintings")
        assert cosine_similarity(vector, vector) == pytest.approx(1.0, abs=1e-5)

    def test_shared_vocabulary_scores_higher_than_unrelated_text(self) -> None:
        art = hash_embed("art gallery museum paintings sculpture exhibition")
        similar = hash_embed("museum exhibition of paintings and sculpture")
        unrelated = hash_embed("mountain hiking trail backpacking summit ridge")
        assert cosine_similarity(art, similar) > cosine_similarity(art, unrelated)

    def test_empty_text_yields_a_zero_vector(self) -> None:
        assert cosine_similarity(hash_embed(""), hash_embed("anything")) == 0.0

    def test_stopwords_are_dropped(self) -> None:
        assert "the" not in tokenize("the museum and the gallery")
        assert "museum" in tokenize("the museum and the gallery")


class TestJSONExtraction:
    def test_plain_json(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_after_commentary(self) -> None:
        assert extract_json('Here is the plan:\n{"a": 1}') == {"a": 1}

    def test_returns_none_when_there_is_no_object(self) -> None:
        assert extract_json("no json at all") is None
        assert extract_json("") is None

    def test_ignores_a_bare_array(self) -> None:
        """The contract is a JSON *object*; a list is not a valid reply."""
        assert extract_json("[1, 2, 3]") is None


@pytest.mark.asyncio
class TestStubProvider:
    async def test_is_deterministic(self) -> None:
        from app.ai.llm import LLMMessage

        provider = StubProvider()
        messages = [LLMMessage(role="user", content="plan me a trip")]
        first = await provider.complete(messages, system="TASK:PARSE_INTENT")
        second = await provider.complete(messages, system="TASK:PARSE_INTENT")
        assert first.text == second.text

    async def test_parses_intent_into_the_schema(self) -> None:
        from app.ai.llm import LLMMessage

        provider = StubProvider()
        intent, _ = await provider.complete_json(
            [
                LLMMessage(
                    role="user",
                    content="We love history and museums, at a relaxed pace.",
                )
            ],
            TripIntent,
            system="TASK:PARSE_INTENT",
        )
        assert "history" in [str(i) for i in intent.interests]
        assert str(intent.pace) == "relaxed"

    async def test_detects_a_packed_pace(self) -> None:
        from app.ai.llm import LLMMessage

        intent, _ = await StubProvider().complete_json(
            [LLMMessage(role="user", content="We want a packed schedule, see everything")],
            TripIntent,
            system="TASK:PARSE_INTENT",
        )
        assert str(intent.pace) == "packed"
