"""Sibling-import unit tests; run as `python3 _tests_command.py`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mcc_supervisor as mod  # noqa: E402

TESTS = [
    ("come here please", "follow"),
    ("follow me", "follow"),
    ("stop", "stop"),
    ("stay here", "stop"),
    ("status", "status"),
    ("sleep now", "sleep_now"),
    ("protect me from creeper", "protect_me"),
    ("equip sword", "equip_sword"),
    ("equip axe", "equip_axe"),
    ("equip pickaxe", "equip_pickaxe"),
    ("use shield", "equip_shield"),
    ("drop logs", "drop_logs"),
    ("drop food", "drop_food"),
    ("drop all", "drop_all"),
    ("find trees", "find_trees"),
    ("cut logs", "cut_logs"),
    ("collect wood", "collect_wood"),
    ("bring wood", "bring_wood"),
    ("lumberjack mode", "lumberjack"),
    ("hi", "hello"),
    ("help", "help"),
    ("who is owner", "owner"),
    ("random text bla bla", "chat"),
]


def main() -> int:
    failed = []
    for msg, expected in TESTS:
        got = mod.detect_command(msg)
        status = "OK" if got == expected else "FAIL"
        if got != expected:
            failed.append((msg, expected, got))
        print(f"  {status:4s}  {msg!r:35s} -> {got}  (expected {expected})")

    print(f"\npassed: {len(TESTS) - len(failed)}/{len(TESTS)}")

    # Owner whitelist tests
    os.environ["OWNER_USERNAMES"] = ".Nirankar66, codewithjoy-9876"
    mod.STATE.owners = mod.load_owners()
    print("owners loaded:", sorted(mod.STATE.owners))
    assert mod.is_owner(".Nirankar66")
    assert mod.is_owner(".NIRANKAR66")
    assert mod.is_owner("codewithjoy-9876")
    assert not mod.is_owner("randomguy")
    print("owner whitelist tests PASSED")

    # Bed reason translation
    assert mod.translate_bed_reason("Could not find a bed!") == "nearby bed nahi mila"
    assert mod.translate_bed_reason("This bed is occupied") == "bed occupied hai"
    assert mod.translate_bed_reason("random log") == ""
    print("bed-reason tests PASSED")

    if failed:
        print("\nFAILED CASES:")
        for f in failed:
            print(" ", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
