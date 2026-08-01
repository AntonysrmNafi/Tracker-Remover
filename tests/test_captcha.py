import linkcleaner.captcha as captcha


def test_generate_challenge_has_four_unique_choices():
    challenge = captcha.generate_challenge()
    assert len(challenge.choices) == len(set(challenge.choices))
    assert len(challenge.choices) == 4


def test_generate_challenge_includes_the_correct_answer():
    challenge = captcha.generate_challenge()
    assert challenge.correct_answer in challenge.choices


def test_generate_challenge_choices_are_all_positive():
    for _ in range(50):  # run a bunch to catch edge cases near small operands
        challenge = captcha.generate_challenge()
        assert all(choice > 0 for choice in challenge.choices)


def test_generate_challenge_question_matches_correct_answer():
    for _ in range(20):
        challenge = captcha.generate_challenge()
        a, b = challenge.question.replace("What is ", "").replace("?", "").split(" + ")
        assert int(a) + int(b) == challenge.correct_answer


def test_flood_not_triggered_under_threshold(monkeypatch):
    captcha._link_timestamps.clear()
    assert captcha.check_and_record_flood(1, 5) is False


def test_flood_triggered_over_threshold():
    captcha._link_timestamps.clear()
    assert captcha.check_and_record_flood(1, 6) is True


def test_flood_triggered_by_cumulative_count_across_messages():
    captcha._link_timestamps.clear()
    assert captcha.check_and_record_flood(1, 3) is False
    assert captcha.check_and_record_flood(1, 2) is False  # total 5, still not over
    assert captcha.check_and_record_flood(1, 1) is True   # total 6, over threshold


def test_flood_is_scoped_per_user():
    captcha._link_timestamps.clear()
    assert captcha.check_and_record_flood(1, 6) is True
    assert captcha.check_and_record_flood(2, 1) is False


def test_flood_old_events_fall_out_of_window(monkeypatch):
    captcha._link_timestamps.clear()
    fake_time = [1000.0]
    monkeypatch.setattr(captcha.time, "monotonic", lambda: fake_time[0])

    assert captcha.check_and_record_flood(1, 5) is False

    fake_time[0] += captcha.FLOOD_WINDOW_SECONDS + 1  # advance past the window
    assert captcha.check_and_record_flood(1, 5) is False  # old ones expired, fresh start
