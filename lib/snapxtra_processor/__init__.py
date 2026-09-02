"""SBAS multi-interferogram processor."""
from lib.snapxtra_processor.mixin_core import SnapxtraCoreMixin
from lib.snapxtra_processor.mixin_pairs import PairsMixin
from lib.snapxtra_processor.mixin_stamps_helpers import StampsHelpersMixin
from lib.snapxtra_processor.mixin_aoi import AoiMixin
from lib.snapxtra_processor.mixin_steps_early import StepsEarlyMixin
from lib.snapxtra_processor.mixin_layout import LayoutMixin
from lib.snapxtra_processor.mixin_snaphu import SnaphuMixin
from lib.snapxtra_processor.mixin_geoc import GeocMixin
from lib.snapxtra_processor.mixin_steps_stamps import StepsStampsMixin
from lib.snapxtra_processor.mixin_runner import RunnerMixin


class SBASMultiProcessor(
    RunnerMixin,
    StepsStampsMixin,
    GeocMixin,
    SnaphuMixin,
    LayoutMixin,
    StepsEarlyMixin,
    AoiMixin,
    StampsHelpersMixin,
    PairsMixin,
    SnapxtraCoreMixin,
):
    pass
