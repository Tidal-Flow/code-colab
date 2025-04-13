import base64
import io
from pydub import AudioSegment

def serialize(audio_path):
    """
    Serialize an audio file to base64 string
    
    Args:
        audio_path (str): Path to the audio file
        
    Returns:
        str: Base64 encoded string of the audio file
    """
    with open(audio_path, "rb") as audio_file:
        return base64.b64encode(audio_file.read()).decode('utf-8')

def deserialize(base64_string):
    """
    Deserialize a base64 string to an AudioSegment
    
    Args:
        base64_string (str): Base64 encoded string of the audio file
        
    Returns:
        AudioSegment: AudioSegment object
    """
    audio_bytes = base64.b64decode(base64_string)
    return AudioSegment.from_file(io.BytesIO(audio_bytes))

def get_audio_metadata(audio_segment):
    """
    Get metadata from an AudioSegment
    
    Args:
        audio_segment (AudioSegment): AudioSegment object
        
    Returns:
        dict: Dictionary containing metadata
    """
    return {
        'duration': len(audio_segment) / 1000,  # in seconds
        'sample_rate': audio_segment.frame_rate,
        'channels': audio_segment.channels,
        'frame_width': audio_segment.sample_width,
        'frame_count': int(len(audio_segment) * audio_segment.frame_rate / 1000)
    } 