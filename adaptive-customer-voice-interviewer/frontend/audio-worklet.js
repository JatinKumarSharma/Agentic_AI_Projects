
class PCM16ResamplerProcessor extends AudioWorkletProcessor {
    constructor() {
        super();

        this.targetSampleRate = 16000;
        this.sourceSampleRate = sampleRate;

        this.sourceStep =
            this.sourceSampleRate / this.targetSampleRate;

        // Position of the next output sample relative to
        // the beginning of the current input block.
        this.position = 0;

        // Retain the final input sample for interpolation
        // across consecutive processing blocks.
        this.previousSample = 0;
        this.hasPreviousSample = false;
    }

    process(inputs, outputs) {
        const channels = inputs[0];

        if (!channels || channels.length === 0 || channels[0].length === 0) {
            return true;
        }

        const frameCount = channels[0].length;

        // Convert the input to mono by averaging all channels.
        const mono = new Float32Array(frameCount);

        for (let frame = 0; frame < frameCount; frame++) {
            let sum = 0;

            for (let channel = 0; channel < channels.length; channel++) {
                sum += channels[channel][frame] || 0;
            }

            mono[frame] = sum / channels.length;
        }

        // Maximum possible output samples for this block.
        const maxOutputSamples =
            Math.ceil(frameCount / this.sourceStep) + 2;

        const output = new Int16Array(maxOutputSamples);
        let outputCount = 0;

        const getSample = (index) => {
            if (index === -1 && this.hasPreviousSample) {
                return this.previousSample;
            }

            if (index < 0 || index >= frameCount) {
                return 0;
            }

            return mono[index];
        };

        // Linear interpolation with persistent phase.
        while (this.position + 1 < frameCount) {
            const index = Math.floor(this.position);
            const fraction = this.position - index;

            const sampleA = getSample(index);
            const sampleB = getSample(index + 1);

            const interpolated =
                sampleA + (sampleB - sampleA) * fraction;

            const clamped = Math.max(
                -1,
                Math.min(1, interpolated)
            );

            output[outputCount++] = Math.round(
                clamped < 0
                    ? clamped * 32768
                    : clamped * 32767
            );

            this.position += this.sourceStep;
        }

        // Carry the next output position into the next block.
        this.position -= frameCount;

        this.previousSample = mono[frameCount - 1];
        this.hasPreviousSample = true;

        if (outputCount > 0) {
            const pcmBuffer = output.slice(0, outputCount);

            this.port.postMessage(
                pcmBuffer.buffer,
                [pcmBuffer.buffer]
            );
        }

        return true;
    }
}

registerProcessor(
    "pcm16-resampler",
    PCM16ResamplerProcessor
);
