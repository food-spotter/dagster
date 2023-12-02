import datetime
import functools
import operator
from dataclasses import dataclass
from typing import (
    AbstractSet,
    Mapping,
    Optional,
    Sequence,
    Type,
    TypeVar,
)

from dagster._core.definitions.asset_automation_condition_cursor import (
    AssetAutomationConditionCursor,
)
from dagster._core.definitions.data_time import CachingDataTimeResolver
from dagster._core.definitions.events import AssetKey, AssetKeyPartitionKey
from dagster._core.definitions.partition import PartitionsDefinition
from dagster._core.definitions.partition_mapping import IdentityPartitionMapping
from dagster._core.definitions.time_window_partition_mapping import TimeWindowPartitionMapping
from dagster._utils.caching_instance_queryer import CachingInstanceQueryer

from .asset_automation_evaluator import (
    AssetSubsetWithMetadata,
    AutomationCondition,
    ConditionEvaluation,
    ConditionEvaluationResult,
)
from .asset_daemon_cursor import AssetDaemonAssetCursor
from .asset_graph import AssetGraph
from .asset_subset import AssetSubset

T = TypeVar("T")


@dataclass(frozen=True)
class AssetAutomationEvaluationContext:
    asset_key: AssetKey
    asset_cursor: Optional[AssetDaemonAssetCursor]

    instance_queryer: CachingInstanceQueryer
    data_time_resolver: CachingDataTimeResolver

    evaluation_results_by_key: Mapping[AssetKey, ConditionEvaluationResult]

    @property
    def asset_graph(self) -> AssetGraph:
        return self.instance_queryer.asset_graph

    @property
    def partitions_def(self) -> Optional[PartitionsDefinition]:
        return self.asset_graph.get_partitions_def(self.asset_key)

    @property
    def evaluation_time(self) -> datetime.datetime:
        """Returns the time at which this rule is being evaluated."""
        return self.instance_queryer.evaluation_time

    @property
    def latest_evaluation(self) -> Optional[ConditionEvaluation]:
        if not self.asset_cursor:
            return None
        return self.asset_cursor.latest_evaluation

    @property
    def previous_tick_requested_subset(self) -> AssetSubset:
        """Returns the set of asset partitions that were requested on the previous tick."""
        if not self.latest_evaluation:
            return self.empty_subset()
        return self.latest_evaluation.true_subset

    @property
    def previous_tick_candidate_subset(self) -> AssetSubset:
        """Returns the set of asset partitions that were candidates on the previous tick."""
        if not self.latest_evaluation:
            return self.empty_subset()
        return self.latest_evaluation.candidate_subset

    def materializable_in_same_run(self, child_key: AssetKey, parent_key: AssetKey) -> bool:
        """Returns whether a child asset can be materialized in the same run as a parent asset."""
        from dagster._core.definitions.external_asset_graph import ExternalAssetGraph

        return (
            # both assets must be materializable
            child_key in self.asset_graph.materializable_asset_keys
            and parent_key in self.asset_graph.materializable_asset_keys
            # the parent must have the same partitioning
            and self.asset_graph.have_same_partitioning(child_key, parent_key)
            # the parent must have a simple partition mapping to the child
            and (
                not self.asset_graph.is_partitioned(parent_key)
                or isinstance(
                    self.asset_graph.get_partition_mapping(child_key, parent_key),
                    (TimeWindowPartitionMapping, IdentityPartitionMapping),
                )
            )
            # the parent must be in the same repository to be materialized alongside the candidate
            and (
                not isinstance(self.asset_graph, ExternalAssetGraph)
                or self.asset_graph.get_repository_handle(child_key)
                == self.asset_graph.get_repository_handle(parent_key)
            )
        )

    def get_parents_that_will_not_be_materialized_on_current_tick(
        self, *, asset_partition: AssetKeyPartitionKey
    ) -> AbstractSet[AssetKeyPartitionKey]:
        """Returns the set of parent asset partitions that will not be updated in the same run of
        this asset partition if a run is launched for this asset partition on this tick.
        """
        return {
            parent
            for parent in self.asset_graph.get_parents_partitions(
                dynamic_partitions_store=self.instance_queryer,
                current_time=self.instance_queryer.evaluation_time,
                asset_key=asset_partition.asset_key,
                partition_key=asset_partition.partition_key,
            ).parent_partitions
            if parent not in self.parent_will_update_subset
            or not self.materializable_in_same_run(asset_partition.asset_key, parent.asset_key)
        }

    @functools.cached_property
    def parent_will_update_subset(self) -> AssetSubset:
        """Returns the set of asset partitions whose parents will be updated on this tick, and which
        can be materialized in the same run as this asset.
        """
        subset = self.empty_subset()
        for parent_key in self.asset_graph.get_parents(self.asset_key):
            if not self.materializable_in_same_run(self.asset_key, parent_key):
                continue
            parent_result = self.evaluation_results_by_key.get(parent_key)
            if not parent_result:
                continue
            parent_subset = parent_result.evaluation.true_subset
            subset |= parent_subset
        return subset

    def for_root_condition(
        self, root_condition: AutomationCondition
    ) -> "AssetAutomationConditionEvaluationContext":
        return AssetAutomationConditionEvaluationContext(
            asset_context=self,
            condition=root_condition,
            candidates_subset=AssetSubset.all(
                asset_key=self.asset_key,
                partitions_def=self.partitions_def,
                dynamic_partitions_store=self.instance_queryer,
                current_time=self.instance_queryer.evaluation_time,
            ),
            cursor=self.asset_cursor.condition_cursor if self.asset_cursor else None,
            previous_condition_evaluation=self.asset_cursor.latest_evaluation
            if self.asset_cursor
            else None,
        )

    def empty_subset(self) -> AssetSubset:
        return AssetSubset.empty(self.asset_key, self.partitions_def)


@dataclass(frozen=True)
class AssetAutomationConditionEvaluationContext:
    asset_context: AssetAutomationEvaluationContext
    condition: AutomationCondition
    candidates_subset: AssetSubset
    cursor: Optional[AssetAutomationConditionCursor]
    previous_condition_evaluation: Optional["ConditionEvaluation"]

    @property
    def asset_key(self) -> AssetKey:
        return self.asset_context.asset_key

    @property
    def partitions_def(self) -> Optional[PartitionsDefinition]:
        return self.asset_context.partitions_def

    @property
    def instance_queryer(self) -> CachingInstanceQueryer:
        return self.asset_context.instance_queryer

    @property
    def max_storage_id(self) -> Optional[int]:
        return self.cursor.max_storage_id if self.cursor else None

    @property
    def previous_tick_true_subset(self) -> AssetSubset:
        """Returns the set of asset partitions that were true on the previous tick."""
        if not self.previous_condition_evaluation:
            return self.empty_subset()
        return self.previous_condition_evaluation.true_subset

    @property
    def parent_has_updated_subset(self) -> AssetSubset:
        """Returns the set of asset partitions whose parents have updated since the last time this
        condition was evaluated.
        """
        return AssetSubset.from_asset_partitions_set(
            self.asset_key,
            self.partitions_def,
            self.asset_context.instance_queryer.asset_partitions_with_newly_updated_parents(
                latest_storage_id=self.cursor.max_storage_id if self.cursor else None,
                child_asset_key=self.asset_context.asset_key,
                map_old_time_partitions=False,
            ),
        )

    @property
    def candidate_parent_has_or_will_update_subset(self) -> AssetSubset:
        """Returns the set of candidates for this tick which have parents that have updated since
        the previous tick, or will update on this tick.
        """
        return self.candidates_subset & (
            self.parent_has_updated_subset | self.asset_context.parent_will_update_subset
        )

    @property
    def candidates_not_evaluated_on_previous_tick_subset(self) -> AssetSubset:
        """Returns the set of candidates for this tick which were not candidates on the previous
        tick.
        """
        if not self.previous_condition_evaluation:
            return self.candidates_subset
        return self.candidates_subset - self.previous_condition_evaluation.candidate_subset

    @property
    def materialized_since_previous_tick_subset(self) -> AssetSubset:
        """Returns the set of asset partitions that were materialized since the previous tick."""
        return AssetSubset.from_asset_partitions_set(
            self.asset_key,
            self.partitions_def,
            self.asset_context.instance_queryer.get_asset_partitions_updated_after_cursor(
                self.asset_key,
                asset_partitions=None,
                after_cursor=self.max_storage_id,
                respect_materialization_data_versions=False,
            ),
        )

    def get_cursor_extra(self, key: str, astype: Type[T]) -> Optional[T]:
        """Returns a value from the cursor's extras dictionary, if it exists and is of the given
        type.
        """
        val = self.cursor.extras.get(key) if self.cursor else None
        if val is None or not isinstance(val, astype):
            return None
        return val

    def empty_subset(self) -> AssetSubset:
        return self.asset_context.empty_subset()

    def combine_previous_data(
        self,
        new_subsets_with_metadata: Sequence[AssetSubsetWithMetadata],
        previous_subset_to_ignore: AssetSubset,
    ) -> Sequence[AssetSubsetWithMetadata]:
        # this is the subset of the asset for which we have new metadata
        subset_with_new_metadata: AssetSubset = functools.reduce(
            operator.or_,
            [s.asset_subset for s in new_subsets_with_metadata],
            initial=self.empty_subset(),
        )

        # remove subsets that are now covered by new metadata
        subset_to_ignore = previous_subset_to_ignore | subset_with_new_metadata
        filtered_previous_subsets = []
        previous_subsets = (
            self.previous_condition_evaluation.subsets_with_metadata
            if self.previous_condition_evaluation
            else []
        )
        for subset in previous_subsets:
            filtered_previous_subsets.append(
                subset._replace(asset_subset=subset.asset_subset - subset_to_ignore)
            )

        # TODO: compact subsets with matching metadata
        return [*filtered_previous_subsets, *new_subsets_with_metadata]

    def for_child(
        self, condition: AutomationCondition, candidates_subset: AssetSubset
    ) -> "AssetAutomationConditionEvaluationContext":
        return AssetAutomationConditionEvaluationContext(
            asset_context=self.asset_context,
            condition=condition,
            candidates_subset=candidates_subset,
            cursor=self.cursor.for_child(condition) if self.cursor else None,
            previous_condition_evaluation=self.previous_condition_evaluation.for_child(condition)
            if self.previous_condition_evaluation
            else None,
        )
