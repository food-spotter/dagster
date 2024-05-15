import {gql, useQuery} from '@apollo/client';
import 'chartjs-adapter-date-fns';
import {
  Box,
  Button,
  ButtonLink,
  Dialog,
  DialogFooter,
  DialogHeader,
  MiddleTruncate,
  Spinner,
  Subtitle2,
  Tab,
  Table,
  Tabs,
  Tag,
} from '@dagster-io/ui-components';
import {useMemo, useState} from 'react';

import {RunList, TargetedRunList} from './InstigationTick';
import {HISTORY_TICK_FRAGMENT} from './InstigationUtils';
import {HistoryTickFragment} from './types/InstigationUtils.types';
import {
  BackfillSelectedTickQuery,
  BackfillSelectedTickQueryVariables,
  JobSelectedTickQuery,
  JobSelectedTickQueryVariables,
} from './types/TickDetailsDialog.types';
import {showCustomAlert} from '../app/CustomAlertProvider';
import {PythonErrorInfo} from '../app/PythonErrorInfo';
import {formatElapsedTimeWithoutMsec} from '../app/Util';
import {Timestamp} from '../app/time/Timestamp';
import {AssetDaemonTickFragment} from '../assets/auto-materialization/types/AssetDaemonTicksQuery.types';
import {
  DynamicPartitionsRequestResult,
  DynamicPartitionsRequestType,
  InstigationSelector,
  InstigationTickStatus,
} from '../graphql/types';
import {TimestampDisplay} from '../schedules/TimestampDisplay';
import {QueryfulTickLogsTable} from '../ticks/TickLogDialog';
import {TickSource} from '../ticks/useTickWithLogs';

interface DialogProps extends InnerProps {
  onClose: () => void;
  isOpen: boolean;
}

export const TickDetailsDialog = ({tickId, isOpen, tickSource, onClose}: DialogProps) => {
  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      style={{width: '80vw', maxWidth: '1200px', minWidth: '600px'}}
    >
      <TickDetailsDialogImpl tickId={tickId} tickSource={tickSource} />
      {/* The logs table uses z-index for the column lines. Create a new stacking index
      for the footer so that the lines don't sit above it. */}
      <div style={{zIndex: 1}}>
        <DialogFooter topBorder>
          <Button onClick={onClose}>Close</Button>
        </DialogFooter>
      </div>
    </Dialog>
  );
};

interface InnerProps {
  tickId: number | undefined;
  tickSource: TickSource;
}

const TickDetailsDialogImpl = ({tickId, tickSource}: InnerProps) => {
  const [activeTab, setActiveTab] = useState<'result' | 'logs'>('result');

  const {data: dataInstigation} = useQuery<JobSelectedTickQuery, JobSelectedTickQueryVariables>(
    JOB_SELECTED_TICK_QUERY,
    {
      variables: {
        instigationSelector: tickSource as InstigationSelector,
        tickId: tickId || 0,
      },
      skip: !tickId || 'backfillId' in tickSource,
    },
  );
  const {data: dataBackfill} = useQuery<
    BackfillSelectedTickQuery,
    BackfillSelectedTickQueryVariables
  >(BACKFILL_SELECTED_TICK_QUERY, {
    variables: {
      backfillId: 'backfillId' in tickSource ? tickSource.backfillId : '',
      tickId: tickId || 0,
    },
    skip: !tickId || !('backfillId' in tickSource),
  });

  const tick =
    dataInstigation?.instigationStateOrError.__typename === 'InstigationState'
      ? dataInstigation?.instigationStateOrError.tick
      : dataBackfill?.partitionBackfillOrError.__typename === 'PartitionBackfill'
      ? dataBackfill?.partitionBackfillOrError.tick
      : undefined;

  const [addedPartitionRequests, deletedPartitionRequests] = useMemo(() => {
    const added = tick?.dynamicPartitionsRequestResults.filter(
      (request) =>
        request.type === DynamicPartitionsRequestType.ADD_PARTITIONS &&
        request.partitionKeys?.length,
    );
    const deleted = tick?.dynamicPartitionsRequestResults.filter(
      (request) =>
        request.type === DynamicPartitionsRequestType.DELETE_PARTITIONS &&
        request.partitionKeys?.length,
    );
    return [added, deleted];
  }, [tick?.dynamicPartitionsRequestResults]);

  if (!tick) {
    return (
      <Box style={{padding: 32}} flex={{alignItems: 'center', justifyContent: 'center'}}>
        <Spinner purpose="section" />
      </Box>
    );
  }

  return (
    <>
      <DialogHeader
        label={
          <TimestampDisplay
            timestamp={tick.timestamp}
            timeFormat={{showTimezone: false, showSeconds: true}}
          />
        }
      />
      <Box padding={{vertical: 12, horizontal: 24}} border="bottom">
        <TickDetailSummary tick={tick} />
      </Box>
      <Box padding={{horizontal: 24}} border="bottom">
        <Tabs selectedTabId={activeTab} onChange={setActiveTab}>
          <Tab id="result" title="Result" />
          <Tab id="logs" title="Logs" />
        </Tabs>
      </Box>
      {activeTab === 'result' ? (
        <div style={{height: '500px', overflowY: 'auto'}}>
          {tick.runIds.length ? (
            <>
              <Box padding={{vertical: 12, horizontal: 24}} border="bottom">
                <Subtitle2>Requested Runs</Subtitle2>
              </Box>
              <RunList runIds={tick.runIds} />
            </>
          ) : tick.originRunIds.length ? (
            <TargetedRunList originRunIds={tick.originRunIds} />
          ) : null}
          {addedPartitionRequests?.length ? (
            <>
              <Box padding={{vertical: 12, horizontal: 24}} border="bottom">
                <Subtitle2>Added partitions</Subtitle2>
              </Box>
              <PartitionsTable partitions={addedPartitionRequests} />
            </>
          ) : null}
          {deletedPartitionRequests?.length ? (
            <>
              <Box padding={{vertical: 12, horizontal: 24}} border="bottom">
                <Subtitle2>Deleted partitions</Subtitle2>
              </Box>
              <PartitionsTable partitions={deletedPartitionRequests} />
            </>
          ) : null}
          {tick.error ? (
            <Box padding={24}>
              <PythonErrorInfo error={tick.error} />
            </Box>
          ) : null}
          {tick.skipReason ? (
            <Box padding={24}>
              <strong>Skip reason:</strong> {tick.skipReason}
            </Box>
          ) : null}
        </div>
      ) : null}
      {activeTab === 'logs' ? <QueryfulTickLogsTable tickSource={tickSource} tick={tick} /> : null}
    </>
  );
};

export function TickDetailSummary({tick}: {tick: HistoryTickFragment | AssetDaemonTickFragment}) {
  const intent = useMemo(() => {
    switch (tick?.status) {
      case InstigationTickStatus.FAILURE:
        return 'danger';
      case InstigationTickStatus.STARTED:
        return 'primary';
      case InstigationTickStatus.SUCCESS:
        return 'success';
    }
    return undefined;
  }, [tick]);

  const isAssetDaemonTick = 'requestedAssetMaterializationCount' in tick;

  return (
    <>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12}}>
        <Box flex={{direction: 'column', gap: 4}}>
          <Subtitle2>Status</Subtitle2>
          <Box flex={{direction: 'row', gap: 4, alignItems: 'center'}}>
            <Tag intent={intent}>
              {tick.status === InstigationTickStatus.STARTED ? (
                'Evaluating…'
              ) : (
                <>
                  {(isAssetDaemonTick
                    ? tick.requestedAssetMaterializationCount
                    : tick.runIds.length) ?? 0}{' '}
                  requested
                </>
              )}
            </Tag>
            {tick.error ? (
              <ButtonLink
                onClick={() => {
                  showCustomAlert({
                    title: 'Tick error',
                    body: <PythonErrorInfo error={tick.error!} />,
                  });
                }}
              >
                View error
              </ButtonLink>
            ) : null}
          </Box>
        </Box>
        <Box flex={{direction: 'column', gap: 4}}>
          <Subtitle2>Timestamp</Subtitle2>
          <div>
            {tick ? (
              <Timestamp timestamp={{unix: tick.timestamp}} timeFormat={{showTimezone: true}} />
            ) : (
              '–'
            )}
          </div>
        </Box>
        <Box flex={{direction: 'column', gap: 4}}>
          <Subtitle2>Duration</Subtitle2>
          <div>
            {tick?.endTimestamp
              ? formatElapsedTimeWithoutMsec(tick.endTimestamp * 1000 - tick.timestamp * 1000)
              : '\u2013'}
          </div>
        </Box>
      </div>
    </>
  );
}

function PartitionsTable({partitions}: {partitions: DynamicPartitionsRequestResult[]}) {
  return (
    <Table>
      <thead>
        <tr>
          <th>Partition definition</th>
          <th>Partition</th>
        </tr>
      </thead>
      <tbody>
        {partitions.flatMap(
          (partition) =>
            partition.partitionKeys?.map((key) => (
              <tr key={key}>
                <td>
                  <MiddleTruncate text={partition.partitionsDefName} />
                </td>
                <td>
                  <MiddleTruncate text={key} />
                </td>
              </tr>
            )),
        )}
      </tbody>
    </Table>
  );
}

const JOB_SELECTED_TICK_QUERY = gql`
  query JobSelectedTickQuery($instigationSelector: InstigationSelector!, $tickId: Int!) {
    instigationStateOrError(instigationSelector: $instigationSelector) {
      ... on InstigationState {
        id
        tick(tickId: $tickId) {
          id
          ...HistoryTick
        }
      }
    }
  }
  ${HISTORY_TICK_FRAGMENT}
`;

const BACKFILL_SELECTED_TICK_QUERY = gql`
  query BackfillSelectedTickQuery($backfillId: String!, $tickId: Int!) {
    partitionBackfillOrError(backfillId: $backfillId) {
      ... on PartitionBackfill {
        id
        tick(tickId: $tickId) {
          id
          ...HistoryTick
        }
      }
    }
  }
  ${HISTORY_TICK_FRAGMENT}
`;
