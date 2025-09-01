const express = require('express');
const cors = require('cors');
const axios = require('axios');

const app = express();
app.use(cors());
app.use(express.json());

app.post('/download', async (req, res) => {
    const { url } = req.body;
    if (!url) return res.status(400).json({ error: 'No URL provided' });

    try {
        // TikWM API
        const response = await axios.get(`https://www.tikwm.com/api/?url=${encodeURIComponent(url)}`);
        const data = response.data;

        if (!data || !data.data || !data.data.play) {
            return res.status(404).json({ error: 'No video found' });
        }

        res.json({
            success: true,
            videoUrl: data.data.play,
            cover: data.data.cover,
            music: data.data.music
        });
    } catch (err) {
        console.error(err.message);
        res.status(500).json({ error: 'Failed to fetch video' });
    }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`TikTok backend running on port ${PORT}`)); 