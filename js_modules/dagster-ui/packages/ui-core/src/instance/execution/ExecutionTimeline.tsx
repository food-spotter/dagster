import {Box, useViewport} from '@dagster-io/ui-components';
import {useVirtualizer} from '@tanstack/react-virtual';
import React from 'react';
import {Link} from 'react-router-dom';

import {RunStatusDot} from '../../runs/RunStatusDots';
import {
  CONSTANTS,
  RunChunk,
  RunChunks,
  RunsEmptyOrLoading,
  TimeDividers,
  TimelineRowContainer,
  TimelineRun,
} from '../../runs/RunTimeline';
import {TimeElapsed} from '../../runs/TimeElapsed';
import {RunBatch, batchRunsForTimeline} from '../../runs/batchRunsForTimeline';
import {mergeStatusToBackground} from '../../runs/mergeStatusToBackground';
import {Container, Inner} from '../../ui/VirtualizedTable';

const {DATE_TIME_HEIGHT, ONE_HOUR_MSEC, EMPTY_STATE_HEIGHT, LEFT_SIDE_SPACE_ALLOTTED} = CONSTANTS;

type Props = {
  loading?: boolean;
  runs: TimelineRun[];
  range: [number, number];
};

export const ExecutionTimeline = (props: Props) => {
  const {loading = false, runs, range} = props;
  const parentRef = React.useRef<HTMLDivElement | null>(null);
  const {
    viewport: {width, height},
    containerProps: {ref: measureRef},
  } = useViewport();

  const rowVirtualizer = useVirtualizer({
    count: runs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (_: number) => 32,
    overscan: 40,
  });

  const totalHeight = rowVirtualizer.getTotalSize();
  const items = rowVirtualizer.getVirtualItems();

  if (!width) {
    return <div style={{height: DATE_TIME_HEIGHT + EMPTY_STATE_HEIGHT}} ref={measureRef} />;
  }

  return (
    <>
      <Box
        padding={{left: 24}}
        flex={{direction: 'column', justifyContent: 'center'}}
        style={{fontSize: '16px', flex: `0 0 ${DATE_TIME_HEIGHT}px`}}
        border="top-and-bottom"
      />
      <div style={{position: 'relative'}}>
        <TimeDividers
          interval={range[1] - range[0] > ONE_HOUR_MSEC * 4 ? ONE_HOUR_MSEC : ONE_HOUR_MSEC / 6}
          range={range}
          height={runs.length > 0 ? height : 0}
        />
      </div>
      {runs.length ? (
        <div ref={measureRef} style={{overflow: 'hidden', position: 'relative'}}>
          <Container ref={parentRef}>
            <Inner $totalHeight={totalHeight}>
              {items.map(({index, key, size, start}) => (
                <ExecutionTimelineRow
                  key={key}
                  run={runs[index]!}
                  top={start}
                  height={size}
                  range={range}
                  width={width}
                />
              ))}
            </Inner>
          </Container>
        </div>
      ) : (
        <div ref={measureRef}>
          <RunsEmptyOrLoading loading={loading} includesTicks={false} />
        </div>
      )}
    </>
  );
};

export const ExecutionTimelineRow = ({
  run,
  top,
  height,
  range,
  width: containerWidth,
}: {
  run: TimelineRun;
  top: number;
  height: number;
  range: [number, number];
  width: number;
}) => {
  const [start, end] = range;
  const width = containerWidth - LEFT_SIDE_SPACE_ALLOTTED;

  const chunk = React.useMemo(() => {
    const batches: RunBatch<TimelineRun>[] = batchRunsForTimeline({
      runs: [run],
      start,
      end,
      width,
      minChunkWidth: 4,
      minMultipleWidth: 4,
    });

    return batches[0];
  }, [run, start, end, width]);

  return (
    <TimelineRowContainer $height={height} $start={top}>
      <Box
        style={{width: LEFT_SIDE_SPACE_ALLOTTED}}
        padding={{horizontal: 24}}
        flex={{justifyContent: 'space-between', alignItems: 'center'}}
      >
        <Box flex={{alignItems: 'center', gap: 4}}>
          <RunStatusDot status={run.status} size={12} />
          <Link to={`/runs/${run.id}`}>{run.id.slice(0, 8)}</Link>
        </Box>
        <TimeElapsed startUnix={run.startTime / 1000} endUnix={run.endTime / 1000} />
      </Box>
      <RunChunks>
        {chunk && (
          <RunChunk
            $background={mergeStatusToBackground(chunk.runs)}
            $multiple={false}
            style={{
              left: `${chunk.left}px`,
              width: `${chunk.width}px`,
            }}
          />
        )}
      </RunChunks>
    </TimelineRowContainer>
  );
};
