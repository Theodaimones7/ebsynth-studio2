import tempfile
import unittest
from pathlib import Path

from gui.ebsynth_gui import PlanError, RunOptions, command_for, frame_number, make_plan


def options(root: Path, start=None, end=None) -> RunOptions:
    executable = root / "ebsynth.exe"
    executable.touch()
    return RunOptions(
        executable=executable,
        frames_dir=root / "frames",
        keys_dir=root / "keys",
        output_dir=root / "output",
        start_frame=start, end_frame=end,
        style_weight=1.0, guide_weight=1.25, uniformity=3500.0,
        patch_size=5, pyramid_levels=None, search_vote_iters=6,
        patch_match_iters=4, stop_threshold=5, backend="cpu",
        extra_pass=False, overwrite=False,
    )


class PlanningTests(unittest.TestCase):
    def make_project(self, root: Path, frames: list[int], keys: list[int]) -> RunOptions:
        (root / "frames").mkdir()
        (root / "keys").mkdir()
        for number in frames:
            (root / "frames" / f"shot_{number:04}.png").touch()
        for number in keys:
            (root / "keys" / f"paint-{number}.png").touch()
        return options(root)

    def test_last_number_in_stem_is_frame_number(self):
        self.assertEqual(frame_number(Path("scene2_frame_0042.png")), 42)
        self.assertIsNone(frame_number(Path("frame.png")))

    def test_nearest_keyframe_owns_target(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self.make_project(Path(folder), list(range(1, 10)), [1, 9])
            assignments = {job.target.number: job.key_style.number for job in make_plan(config).jobs}
            self.assertEqual(assignments[4], 1)
            self.assertEqual(assignments[5], 1)
            self.assertEqual(assignments[6], 9)

    def test_range_is_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self.make_project(Path(folder), list(range(10, 16)), [10, 15])
            config = RunOptions(**{**config.__dict__, "start_frame": 12, "end_frame": 14})
            self.assertEqual([j.target.number for j in make_plan(config).jobs], [12, 13, 14])

    def test_missing_source_for_keyframe_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self.make_project(Path(folder), [1, 2, 3], [1, 4])
            with self.assertRaisesRegex(PlanError, "4"):
                make_plan(config)

    def test_command_keeps_paths_as_separate_arguments(self):
        with tempfile.TemporaryDirectory(prefix="eb synth ") as folder:
            config = self.make_project(Path(folder), [1], [1])
            job = make_plan(config).jobs[0]
            command = command_for(job, config)
            self.assertIn(str(job.key_style.path), command)
            self.assertIn(str(job.output), command)
            self.assertEqual(command[-2], "-output")


if __name__ == "__main__":
    unittest.main()
