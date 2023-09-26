from contextlib import contextmanager
from dataclasses import dataclass
from queue import Queue
from typing import Any, Iterator, Mapping, Optional, Set, Union

from dagster_pipes import (
    DAGSTER_PIPED_ENV_KEYS,
    IS_DAGSTER_PIPED_PROCESS_ENV_VAR,
    PIPEABLE_METADATA_TYPE_INFER,
    PipesDataProvenance,
    PipesExtras,
    PipesMessage,
    PipesMetadataType,
    PipesMetadataValue,
    PipesParams,
    PipesProcessContextData,
    PipesTimeWindow,
    encode_env_var,
)
from typing_extensions import TypeAlias

import dagster._check as check
from dagster._core.definitions.asset_check_result import AssetCheckResult
from dagster._core.definitions.asset_check_spec import AssetCheckSeverity
from dagster._core.definitions.data_version import DataProvenance, DataVersion
from dagster._core.definitions.events import AssetKey
from dagster._core.definitions.metadata import MetadataValue, normalize_metadata_value
from dagster._core.definitions.partition_key_range import PartitionKeyRange
from dagster._core.definitions.result import MaterializeResult
from dagster._core.definitions.time_window_partitions import TimeWindow
from dagster._core.execution.context.compute import OpExecutionContext
from dagster._core.execution.context.invocation import BoundOpExecutionContext
from dagster._core.pipes.client import ExtMessageReader

ExtResult: TypeAlias = Union[MaterializeResult, AssetCheckResult]


class ExtMessageHandler:
    def __init__(self, context: OpExecutionContext) -> None:
        self._context = context
        # Queue is thread-safe
        self._result_queue: Queue[ExtResult] = Queue()
        # Only read by the main thread after all messages are handled, so no need for a lock
        self._unmaterialized_assets: Set[AssetKey] = set(context.selected_asset_keys)

    @contextmanager
    def handle_messages(self, message_reader: ExtMessageReader) -> Iterator[PipesParams]:
        with message_reader.read_messages(self) as params:
            yield params
        for key in self._unmaterialized_assets:
            self._result_queue.put(MaterializeResult(asset_key=key))

    def clear_result_queue(self) -> Iterator[ExtResult]:
        while not self._result_queue.empty():
            yield self._result_queue.get()

    def _resolve_metadata(
        self, metadata: Mapping[str, PipesMetadataValue]
    ) -> Mapping[str, MetadataValue]:
        return {
            k: self._resolve_metadata_value(v["raw_value"], v["type"]) for k, v in metadata.items()
        }

    def _resolve_metadata_value(
        self, value: Any, metadata_type: PipesMetadataType
    ) -> MetadataValue:
        if metadata_type == PIPEABLE_METADATA_TYPE_INFER:
            return normalize_metadata_value(value)
        elif metadata_type == "text":
            return MetadataValue.text(value)
        elif metadata_type == "url":
            return MetadataValue.url(value)
        elif metadata_type == "path":
            return MetadataValue.path(value)
        elif metadata_type == "notebook":
            return MetadataValue.notebook(value)
        elif metadata_type == "json":
            return MetadataValue.json(value)
        elif metadata_type == "md":
            return MetadataValue.md(value)
        elif metadata_type == "float":
            return MetadataValue.float(value)
        elif metadata_type == "int":
            return MetadataValue.int(value)
        elif metadata_type == "bool":
            return MetadataValue.bool(value)
        elif metadata_type == "dagster_run":
            return MetadataValue.dagster_run(value)
        elif metadata_type == "asset":
            return MetadataValue.asset(AssetKey.from_user_string(value))
        elif metadata_type == "table":
            return MetadataValue.table(value)
        elif metadata_type == "null":
            return MetadataValue.null()
        else:
            check.failed(f"Unexpected metadata type {metadata_type}")

    # Type ignores because we currently validate in individual handlers
    def handle_message(self, message: PipesMessage) -> None:
        if message["method"] == "report_asset_materialization":
            self._handle_report_asset_materialization(**message["params"])  # type: ignore
        elif message["method"] == "report_asset_check":
            self._handle_report_asset_check(**message["params"])  # type: ignore
        elif message["method"] == "log":
            self._handle_log(**message["params"])  # type: ignore

    def _handle_report_asset_materialization(
        self,
        asset_key: str,
        metadata: Optional[Mapping[str, PipesMetadataValue]],
        data_version: Optional[str],
    ) -> None:
        check.str_param(asset_key, "asset_key")
        check.opt_str_param(data_version, "data_version")
        metadata = check.opt_mapping_param(metadata, "metadata", key_type=str)
        resolved_asset_key = AssetKey.from_user_string(asset_key)
        resolved_metadata = self._resolve_metadata(metadata)
        resolved_data_version = None if data_version is None else DataVersion(data_version)
        result = MaterializeResult(
            asset_key=resolved_asset_key,
            metadata=resolved_metadata,
            data_version=resolved_data_version,
        )
        self._result_queue.put(result)
        self._unmaterialized_assets.remove(resolved_asset_key)

    def _handle_report_asset_check(
        self,
        asset_key: str,
        check_name: str,
        success: bool,
        severity: str,
        metadata: Mapping[str, PipesMetadataValue],
    ) -> None:
        check.str_param(asset_key, "asset_key")
        check.str_param(check_name, "check_name")
        check.bool_param(success, "success")
        check.literal_param(severity, "severity", [x.value for x in AssetCheckSeverity])
        metadata = check.opt_mapping_param(metadata, "metadata", key_type=str)
        resolved_asset_key = AssetKey.from_user_string(asset_key)
        resolved_metadata = self._resolve_metadata(metadata)
        resolved_severity = AssetCheckSeverity(severity)
        result = AssetCheckResult(
            asset_key=resolved_asset_key,
            check_name=check_name,
            success=success,
            severity=resolved_severity,
            metadata=resolved_metadata,
        )
        self._result_queue.put(result)

    def _handle_log(self, message: str, level: str = "info") -> None:
        check.str_param(message, "message")
        self._context.log.log(level, message)


def _ext_params_as_env_vars(
    context_injector_params: PipesParams, message_reader_params: PipesParams
) -> Mapping[str, str]:
    return {
        DAGSTER_PIPED_ENV_KEYS["context"]: encode_env_var(context_injector_params),
        DAGSTER_PIPED_ENV_KEYS["messages"]: encode_env_var(message_reader_params),
    }


@dataclass
class ExtOrchestrationContext:
    context_data: PipesProcessContextData
    message_handler: ExtMessageHandler
    context_injector_params: PipesParams
    message_reader_params: PipesParams

    def get_external_process_env_vars(self):
        return {
            DAGSTER_PIPED_ENV_KEYS[IS_DAGSTER_PIPED_PROCESS_ENV_VAR]: encode_env_var(True),
            **_ext_params_as_env_vars(
                context_injector_params=self.context_injector_params,
                message_reader_params=self.message_reader_params,
            ),
        }

    def get_results(self) -> Iterator[ExtResult]:
        yield from self.message_handler.clear_result_queue()


def build_external_execution_context_data(
    context: OpExecutionContext,
    extras: Optional[PipesExtras],
) -> "PipesProcessContextData":
    asset_keys = (
        [_convert_asset_key(key) for key in sorted(context.selected_asset_keys)]
        if context.has_assets_def
        else None
    )
    code_version_by_asset_key = (
        {
            _convert_asset_key(key): context.assets_def.code_versions_by_key[key]
            for key in context.selected_asset_keys
        }
        if context.has_assets_def
        else None
    )
    provenance_by_asset_key = (
        {
            _convert_asset_key(key): _convert_data_provenance(context.get_asset_provenance(key))
            for key in context.selected_asset_keys
        }
        if context.has_assets_def
        else None
    )
    partition_key = context.partition_key if context.has_partition_key else None
    partition_time_window = context.partition_time_window if context.has_partition_key else None
    partition_key_range = context.partition_key_range if context.has_partition_key else None
    return PipesProcessContextData(
        asset_keys=asset_keys,
        code_version_by_asset_key=code_version_by_asset_key,
        provenance_by_asset_key=provenance_by_asset_key,
        partition_key=partition_key,
        partition_key_range=(
            _convert_partition_key_range(partition_key_range) if partition_key_range else None
        ),
        partition_time_window=(
            _convert_time_window(partition_time_window) if partition_time_window else None
        ),
        run_id=context.run_id,
        job_name=None if isinstance(context, BoundOpExecutionContext) else context.job_name,
        retry_number=0 if isinstance(context, BoundOpExecutionContext) else context.retry_number,
        extras=extras or {},
    )


def _convert_asset_key(asset_key: AssetKey) -> str:
    return asset_key.to_user_string()


def _convert_data_provenance(
    provenance: Optional[DataProvenance],
) -> Optional["PipesDataProvenance"]:
    return (
        None
        if provenance is None
        else PipesDataProvenance(
            code_version=provenance.code_version,
            input_data_versions={
                _convert_asset_key(k): v.value for k, v in provenance.input_data_versions.items()
            },
            is_user_provided=provenance.is_user_provided,
        )
    )


def _convert_time_window(
    time_window: TimeWindow,
) -> "PipesTimeWindow":
    return PipesTimeWindow(
        start=time_window.start.isoformat(),
        end=time_window.end.isoformat(),
    )


def _convert_partition_key_range(
    partition_key_range: PartitionKeyRange,
) -> "PipesTimeWindow":
    return PipesTimeWindow(
        start=partition_key_range.start,
        end=partition_key_range.end,
    )
