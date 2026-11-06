"""Tests for ASAP task state machine."""

import pytest
from datetime import datetime, timezone

from asap.errors import InvalidTransitionError
from asap.models.entities import Task
from asap.models.enums import TaskStatus as ModelTaskStatus
from asap.state.machine import TaskStatus, can_transition, transition


@pytest.fixture
def sample_task() -> Task:
    """Create a sample task for testing."""
    now = datetime.now(timezone.utc)
    return Task(
        id="task_01HX5K4N000000000000000000",
        conversation_id="conv_01HX5K3MQVN8000000000000000",
        status=ModelTaskStatus.SUBMITTED,
        created_at=now,
        updated_at=now,
    )


class TestTaskStatus:
    """Test TaskStatus enum definition."""

    def test_all_states_exist(self) -> None:
        """Test that all required states are defined in TaskStatus enum."""
        # Test all states exist
        assert hasattr(TaskStatus, "SUBMITTED")
        assert hasattr(TaskStatus, "WORKING")
        assert hasattr(TaskStatus, "INPUT_REQUIRED")
        assert hasattr(TaskStatus, "COMPLETED")
        assert hasattr(TaskStatus, "FAILED")
        assert hasattr(TaskStatus, "CANCELLED")

        # Test we have exactly 6 states
        assert len(TaskStatus) == 6

        # Test string values
        assert TaskStatus.SUBMITTED.value == "submitted"
        assert TaskStatus.WORKING.value == "working"
        assert TaskStatus.INPUT_REQUIRED.value == "input_required"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_terminal_states_identification(self) -> None:
        """Test identification of terminal states."""
        # Terminal states (no further transitions allowed)
        terminal_states = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}

        # Non-terminal states (can transition further)
        non_terminal_states = {
            TaskStatus.SUBMITTED,
            TaskStatus.WORKING,
            TaskStatus.INPUT_REQUIRED,
        }

        for state in terminal_states:
            assert state.is_terminal()

        for state in non_terminal_states:
            assert not state.is_terminal()

    def test_enum_properties(self) -> None:
        """Test basic enum properties."""
        # Test enum values are strings
        for status in TaskStatus:
            assert isinstance(status.value, str)

        # Test uniqueness
        values = [status.value for status in TaskStatus]
        assert len(values) == len(set(values))

        # Test string representation
        assert str(TaskStatus.SUBMITTED) == "TaskStatus.SUBMITTED"
        assert repr(TaskStatus.WORKING) == "<TaskStatus.WORKING: 'working'>"


class TestCanTransition:
    """Test the can_transition function."""

    def test_can_transition_returns_bool(self) -> None:
        """Test that can_transition returns a boolean."""
        result = can_transition(TaskStatus.SUBMITTED, TaskStatus.WORKING)
        assert isinstance(result, bool)

    def test_can_transition_valid_transitions(self) -> None:
        """Test that can_transition returns True for valid transitions."""
        # Test all valid transitions
        assert can_transition(TaskStatus.SUBMITTED, TaskStatus.WORKING)
        assert can_transition(TaskStatus.SUBMITTED, TaskStatus.CANCELLED)
        assert can_transition(TaskStatus.WORKING, TaskStatus.COMPLETED)
        assert can_transition(TaskStatus.WORKING, TaskStatus.FAILED)
        assert can_transition(TaskStatus.WORKING, TaskStatus.CANCELLED)
        assert can_transition(TaskStatus.WORKING, TaskStatus.INPUT_REQUIRED)
        assert can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.WORKING)
        assert can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.CANCELLED)

    def test_can_transition_invalid_transitions(self) -> None:
        """Test that can_transition returns False for invalid transitions."""
        # Test some invalid transitions
        assert not can_transition(TaskStatus.COMPLETED, TaskStatus.WORKING)
        assert not can_transition(TaskStatus.FAILED, TaskStatus.SUBMITTED)
        assert not can_transition(TaskStatus.CANCELLED, TaskStatus.COMPLETED)
        assert not can_transition(TaskStatus.SUBMITTED, TaskStatus.COMPLETED)
        assert not can_transition(TaskStatus.WORKING, TaskStatus.SUBMITTED)
        assert not can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.SUBMITTED)


class TestValidTransitions:
    """Test valid state transitions."""

    def test_submitted_transitions(self) -> None:
        """Test valid transitions from submitted state."""
        assert can_transition(TaskStatus.SUBMITTED, TaskStatus.WORKING)
        assert can_transition(TaskStatus.SUBMITTED, TaskStatus.CANCELLED)

        # Invalid transitions from submitted
        assert not can_transition(TaskStatus.SUBMITTED, TaskStatus.COMPLETED)
        assert not can_transition(TaskStatus.SUBMITTED, TaskStatus.FAILED)
        assert not can_transition(TaskStatus.SUBMITTED, TaskStatus.INPUT_REQUIRED)

    def test_working_transitions(self) -> None:
        """Test valid transitions from working state."""
        assert can_transition(TaskStatus.WORKING, TaskStatus.COMPLETED)
        assert can_transition(TaskStatus.WORKING, TaskStatus.FAILED)
        assert can_transition(TaskStatus.WORKING, TaskStatus.CANCELLED)
        assert can_transition(TaskStatus.WORKING, TaskStatus.INPUT_REQUIRED)

        # Invalid transitions from working
        assert not can_transition(TaskStatus.WORKING, TaskStatus.SUBMITTED)

    def test_input_required_transitions(self) -> None:
        """Test valid transitions from input_required state."""
        assert can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.WORKING)
        assert can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.CANCELLED)

        # Invalid transitions from input_required
        assert not can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.COMPLETED)
        assert not can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.FAILED)
        assert not can_transition(TaskStatus.INPUT_REQUIRED, TaskStatus.SUBMITTED)

    def test_terminal_states_no_transitions(self) -> None:
        """Test that terminal states cannot transition to any other state."""
        terminal_states = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]

        for terminal_state in terminal_states:
            for target_state in TaskStatus:
                if target_state != terminal_state:
                    assert not can_transition(terminal_state, target_state), (
                        f"Terminal state {terminal_state} should not transition to {target_state}"
                    )


class TestTransition:
    """Test the transition function."""

    def test_transition_valid_change(self, sample_task: Task) -> None:
        """Test successful transition to a valid new status."""
        # Transition from SUBMITTED to WORKING
        updated_task = transition(sample_task, TaskStatus.WORKING)

        # Should return a new task instance (immutability)
        assert updated_task is not sample_task
        assert updated_task.status == ModelTaskStatus.WORKING
        # Other fields should remain the same
        assert updated_task.id == sample_task.id
        assert updated_task.conversation_id == sample_task.conversation_id

    def test_transition_invalid_change_raises_error(self, sample_task: Task) -> None:
        """Test that invalid transitions raise InvalidTransitionError."""
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition(sample_task, TaskStatus.COMPLETED)  # SUBMITTED -> COMPLETED is invalid

        error = exc_info.value
        assert error.code == "asap:protocol/invalid_state"
        assert error.from_state == "submitted"
        assert error.to_state == "completed"
        assert sample_task.id in error.details["task_id"]

    def test_transition_from_working_to_completed(self, sample_task: Task) -> None:
        """Test transition from WORKING to COMPLETED."""
        # First transition to WORKING
        working_task = transition(sample_task, TaskStatus.WORKING)
        assert working_task.status == ModelTaskStatus.WORKING

        # Then transition to COMPLETED
        completed_task = transition(working_task, TaskStatus.COMPLETED)
        assert completed_task.status == ModelTaskStatus.COMPLETED
        assert completed_task is not working_task

    def test_transition_from_working_to_failed(self, sample_task: Task) -> None:
        """Test transition from WORKING to FAILED."""
        # First transition to WORKING
        working_task = transition(sample_task, TaskStatus.WORKING)
        assert working_task.status == ModelTaskStatus.WORKING

        # Then transition to FAILED
        failed_task = transition(working_task, TaskStatus.FAILED)
        assert failed_task.status == ModelTaskStatus.FAILED
        assert failed_task is not working_task

    def test_transition_from_input_required_to_working(self, sample_task: Task) -> None:
        """Test transition from INPUT_REQUIRED back to WORKING."""
        # First transition to WORKING, then to INPUT_REQUIRED
        working_task = transition(sample_task, TaskStatus.WORKING)
        input_task = transition(working_task, TaskStatus.INPUT_REQUIRED)
        assert input_task.status == ModelTaskStatus.INPUT_REQUIRED

        # Then back to WORKING
        back_to_work_task = transition(input_task, TaskStatus.WORKING)
        assert back_to_work_task.status == ModelTaskStatus.WORKING
        assert back_to_work_task is not input_task

    def test_transition_to_terminal_state(self, sample_task: Task) -> None:
        """Test that transitions to terminal states work from valid states."""
        # SUBMITTED -> CANCELLED (terminal)
        working_task = transition(sample_task, TaskStatus.WORKING)
        cancelled_task = transition(working_task, TaskStatus.CANCELLED)

        assert cancelled_task.status == ModelTaskStatus.CANCELLED
        assert cancelled_task.is_terminal()

    def test_transition_preserves_immutability(self, sample_task: Task) -> None:
        """Test that transition returns a new instance, preserving immutability."""
        original_status = sample_task.status

        # Perform transition
        updated_task = transition(sample_task, TaskStatus.WORKING)

        # Original task should be unchanged
        assert sample_task.status == original_status
        # New task should have new status
        assert updated_task.status == ModelTaskStatus.WORKING

    def test_transition_advances_updated_at_and_keeps_created_at(self, sample_task: Task) -> None:
        """A real status change stamps updated_at and leaves created_at alone."""
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        task = sample_task.model_copy(update={"created_at": past, "updated_at": past})

        updated = transition(task, TaskStatus.WORKING)

        assert updated.status == ModelTaskStatus.WORKING
        assert updated.created_at == past
        assert task.updated_at == past
        assert updated.updated_at > past

    def test_transition_rejects_staying_in_the_same_status(self, sample_task: Task) -> None:
        """Repeating the current status is not a transition."""
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition(sample_task, TaskStatus.SUBMITTED)

        error = exc_info.value
        assert error.from_state == "submitted"
        assert error.to_state == "submitted"
        assert sample_task.status == ModelTaskStatus.SUBMITTED
