"""Sending events to Eventstream in batches. Offline: a fake Event Hub producer and a fake azure.eventhub module."""
import json
import sys
import types

import pytest
from hbdata import replay

LIMIT = 1_000_000  # the Event Hub batch size cap used by the fake producer, in bytes


class FakeEvent:
    def __init__(self, body):
        self.body = body


class FakeBatch:
    def __init__(self, limit):
        self.limit, self.size, self.events = limit, 0, []

    def add(self, event):
        n = len(event.body.encode("utf-8"))
        if self.size + n > self.limit:
            raise ValueError("EventDataBatch has reached its size limit")
        self.size += n
        self.events.append(event)


class FakeProducer:
    def __init__(self, limit=LIMIT):
        self.limit, self.sent = limit, []

    def create_batch(self):
        return FakeBatch(self.limit)

    def send_batch(self, batch):
        assert batch.size <= self.limit and batch.events
        self.sent.append(batch)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def envelopes(n, pad=0):
    return [{"event_id": f"EVT-{i:06d}", "simulated_ingested_at": "x", "payload": "p" * pad} for i in range(n)]


def test_21528_events_split_into_batches_under_the_cap_in_order_and_once():
    producer, sendable = FakeProducer(), envelopes(21_528, pad=120)
    assert sum(len(json.dumps(e)) for e in sendable) > LIMIT  # one batch is not enough
    assert replay.send_in_batches(producer, sendable, FakeEvent) == 21_528
    assert len(producer.sent) > 1 and all(b.size <= LIMIT for b in producer.sent)
    ids = [json.loads(e.body)["event_id"] for b in producer.sent for e in b.events]
    assert ids == [e["event_id"] for e in sendable]


def test_a_small_run_is_one_batch_and_an_empty_run_sends_nothing():
    producer = FakeProducer()
    assert replay.send_in_batches(producer, envelopes(5), FakeEvent) == 5 and len(producer.sent) == 1
    producer = FakeProducer()
    assert replay.send_in_batches(producer, [], FakeEvent) == 0 and producer.sent == []


def test_an_event_that_is_too_big_for_any_batch_gives_a_clear_error():
    producer = FakeProducer(limit=500)
    with pytest.raises(ValueError, match=r"EVT-000001.*larger than the Event Hub batch limit"):
        replay.send_in_batches(producer, envelopes(3, pad=10) [:1] + envelopes(3, pad=900)[1:], FakeEvent)
    assert sum(len(b.events) for b in producer.sent) == 1  # the earlier event was sent, none lost or repeated


def test_send_eventstream_uses_the_batching_and_drops_the_simulated_field(monkeypatch):
    producer = FakeProducer()
    client = types.SimpleNamespace(from_connection_string=lambda conn, eventhub_name: producer)
    fake = types.ModuleType("azure.eventhub")
    fake.EventData, fake.EventHubProducerClient = FakeEvent, client
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.eventhub", fake)
    monkeypatch.setenv("HB_EVENTSTREAM_CONNECTION", "Endpoint=sb://fake/;SharedAccessKeyName=x;SharedAccessKey=fake")
    monkeypatch.setenv("HB_EVENTSTREAM_NAME", "fake-hub")
    assert replay.send_eventstream(envelopes(4000, pad=400)) == 4000
    assert len(producer.sent) > 1
    assert all("simulated_ingested_at" not in json.loads(e.body) for b in producer.sent for e in b.events)
