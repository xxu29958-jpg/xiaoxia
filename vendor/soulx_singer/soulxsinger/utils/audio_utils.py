import torch
import torchaudio
import soundfile as sf


def load_wav(wav_path: str, sample_rate: int):
    """Load wav file and resample to target sample rate.

    Args:
        wav_path (str): Path to wav file.
        sample_rate (int): Target sample rate.

    Returns:
        torch.Tensor: Waveform tensor with shape (1, T).
    """
    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(audio.T.copy())
        
    if sr != sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, sample_rate)

    if len(waveform.shape) > 1 and waveform.shape[0] > 1:
        waveform = torch.mean(waveform, dim=0, keepdim=True)
    
    return waveform

        
