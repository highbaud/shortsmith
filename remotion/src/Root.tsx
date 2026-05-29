import { Composition } from "remotion";
import { Short } from "./Short";
import { defaultShortProps, ShortProps } from "./types";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Short"
      component={Short}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={300}
      defaultProps={defaultShortProps}
      calculateMetadata={({ props }: { props: ShortProps }) => {
        const fps = props.fps || 30;
        return {
          durationInFrames: Math.max(1, Math.ceil(props.durationInSeconds * fps)),
          fps,
          width: 1080,
          height: 1920,
        };
      }}
    />
  );
};
